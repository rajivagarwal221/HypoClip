"""Owner-scoped live broadcast capture requests."""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...models import LiveSession, Task
from ...services.billing_service import BillingService, BillingLimitExceeded
from ...workers.job_queue import JobQueue

router = APIRouter(prefix="/live-sessions", tags=["live-sessions"])
ACTIVE = ("queued", "recording", "processing")


def live_platform(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
        invalid_authority = bool(parsed.username or parsed.password or parsed.port)
    except ValueError:
        raise ValueError("Enter a public HTTPS live-stream URL") from None
    if parsed.scheme != "https" or invalid_authority:
        raise ValueError("Enter a public HTTPS live-stream URL")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    if host in {"twitch.tv", "m.twitch.tv"} and len(path.split("/")) == 2 and path[1:].replace("_", "").isalnum():
        if path.lower() not in {"/videos", "/directory", "/search", "/settings"}:
            return "twitch"
    if host == "kick.com" and len(path.split("/")) == 2 and path[1:].replace("-", "").replace("_", "").isalnum():
        if path.lower() not in {"/video", "/category", "/browse"}:
            return "kick"
    if host in {"youtube.com", "m.youtube.com"} and path in {"/watch", "/live"}:
        from ...youtube_utils import get_youtube_video_id

        if get_youtube_video_id(url):
            return "youtube"
    raise ValueError("Enter a Twitch or Kick channel URL, or a YouTube live video URL")


def public_session(row: LiveSession) -> dict:
    return {
        "id": row.id, "platform": row.platform, "url": row.url,
        "status": row.status, "stop_requested": row.stop_requested,
        "chat_available": bool(row.chat_counts), "task_id": row.task_id,
        "task_ids": row.task_ids or [], "youtube_account_id": row.youtube_account_id,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def refresh_status(db: AsyncSession, row: LiveSession) -> None:
    if row.status != "processing" or not row.task_ids:
        return
    tasks = [await db.get(Task, task_id) for task_id in row.task_ids]
    if tasks and all(task and task.status in {"completed", "error", "cancelled"} for task in tasks):
        row.status = "completed" if any(task.status == "completed" for task in tasks) else "error"
        row.error_message = "Some highlights could not be generated" if any(task.status == "error" for task in tasks) else None
        await db.commit()


async def owner(request: Request, db: AsyncSession) -> str:
    return await resolve_authenticated_user_id(request, db, get_config())


@router.post("")
async def create_live_session(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await owner(request, db)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(422, "Send a JSON object with a live-stream URL") from None
    url = payload.get("url") if isinstance(payload, dict) else None
    youtube_account_id = payload.get("youtube_account_id") if isinstance(payload, dict) else None
    if not isinstance(youtube_account_id, str) or not youtube_account_id:
        raise HTTPException(422, "Connect and select a YouTube account before starting public auto-upload")
    if not isinstance(url, str) or len(url) > 1000:
        raise HTTPException(422, "A live-stream URL is required")
    url = url.strip()
    try:
        platform = live_platform(url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    existing = (await db.execute(select(LiveSession).where(
        LiveSession.user_id == user_id, LiveSession.url == url,
        LiveSession.status.in_(ACTIVE),
    ))).scalar_one_or_none()
    if existing:
        return public_session(existing)
    active = (await db.execute(select(LiveSession.id).where(
        LiveSession.user_id == user_id, LiveSession.status.in_(ACTIVE),
    ).limit(1))).scalar_one_or_none()
    if active:
        raise HTTPException(409, "Stop or finish your current stream before starting another")
    try:
        await BillingService(db).assert_can_create_task(user_id)
    except BillingLimitExceeded as exc:
        raise HTTPException(402, exc.summary.get("reason") or "Generation limit reached") from None
    from ...services.social_service import SocialService

    social = SocialService(db)
    if not await social.has_access(user_id):
        raise HTTPException(403, "YouTube publishing is not available on this account")
    account = await social.repo.get_user_account(db, user_id, youtube_account_id)
    if not account or account.get("provider") != "youtube" or account.get("status") != "active" or account.get("revoked_at"):
        raise HTTPException(422, "Select an active connected YouTube account")
    session = LiveSession(id=str(uuid.uuid4()), user_id=user_id, platform=platform, url=url, youtube_account_id=youtube_account_id)
    db.add(session)
    await db.commit()
    try:
        await JobQueue.enqueue_job("capture_live_session", session.id, _queue_name="supoclip_live", _job_id=f"live:{session.id}")
    except Exception:
        session.status = "error"
        session.error_message = "Unable to start recording. Please retry."
        await db.commit()
        raise HTTPException(503, session.error_message) from None
    return public_session(session)


@router.get("")
async def list_live_sessions(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await owner(request, db)
    rows = (await db.execute(select(LiveSession).where(LiveSession.user_id == user_id).order_by(LiveSession.created_at.desc()).limit(30))).scalars().all()
    for row in rows:
        await refresh_status(db, row)
    return [public_session(row) for row in rows]


@router.get("/{session_id}")
async def get_live_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await owner(request, db)
    row = await db.get(LiveSession, session_id)
    if not row or row.user_id != user_id:
        raise HTTPException(404, "Live session not found")
    await refresh_status(db, row)
    return public_session(row)


@router.post("/{session_id}/stop")
async def stop_live_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await owner(request, db)
    row = await db.get(LiveSession, session_id)
    if not row or row.user_id != user_id:
        raise HTTPException(404, "Live session not found")
    if row.status in ACTIVE:
        row.stop_requested = True
        await db.commit()
    return public_session(row)
