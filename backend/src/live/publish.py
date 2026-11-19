"""Queue public YouTube Shorts for live clips, once per clip."""

from __future__ import annotations

import logging

from sqlalchemy import text

from ..database import AsyncSessionLocal
from ..models import LiveSession
from ..repositories.task_repository import TaskRepository
from ..services.social_service import SocialService

logger = logging.getLogger(__name__)


async def publish_ready_clip(task_id: str, clip_id: str) -> None:
    async with AsyncSessionLocal() as db:
        metadata = await TaskRepository.load_source_metadata(db, task_id)
        session_id = (metadata or {}).get("live_session_id")
        if not session_id:
            return
        live = await db.get(LiveSession, session_id)
        if not live or not live.youtube_account_id:
            return
        account_id = live.youtube_account_id
        existing = (await db.execute(text("""
            SELECT id FROM social_posts WHERE clip_id = :clip AND social_account_id = :account
            ORDER BY created_at DESC LIMIT 1
        """), {"clip": clip_id, "account": account_id})).scalar_one_or_none()
        if existing:
            await db.execute(text("UPDATE live_auto_posts SET post_id = :post WHERE clip_id = :clip"), {"post": existing, "clip": clip_id})
            await db.commit()
            return
        claimed = (await db.execute(text("""
            INSERT INTO live_auto_posts (clip_id, account_id) VALUES (:clip, :account)
            ON CONFLICT (clip_id) DO UPDATE SET claimed_at = now()
            WHERE live_auto_posts.post_id IS NULL AND live_auto_posts.claimed_at < now() - INTERVAL '10 minutes'
            RETURNING clip_id
        """), {"clip": clip_id, "account": account_id})).scalar_one_or_none()
        await db.commit()
        if not claimed:
            return
        try:
            service = SocialService(db)
            if not await service.has_access(live.user_id):
                raise ValueError("YouTube publishing is unavailable for this account")
            clip = await service.clip_repo.get_clip_by_id(db, clip_id)
            if not clip:
                raise ValueError("Generated clip is unavailable")
            title = (clip.get("hook_title") or clip.get("text") or "Live highlight").strip()[:95]
            post = await service.create_post(
                live.user_id, task_id=task_id, clip_id=clip_id,
                social_account_id=account_id, title=title,
                caption=(clip.get("text") or "Live highlight").strip()[:1900],
                hashtags=["Shorts"], privacy_level="public", scheduled_for=None,
            )
            await db.execute(text("UPDATE live_auto_posts SET post_id = :post WHERE clip_id = :clip"), {"post": post["id"], "clip": clip_id})
            await db.commit()
        except Exception:
            logger.exception("Could not queue live clip %s for YouTube", clip_id)
            await db.execute(text("DELETE FROM live_auto_posts WHERE clip_id = :clip AND post_id IS NULL"), {"clip": clip_id})
            await db.commit()


async def retry_live_uploads(ctx: dict) -> dict:
    """Retry clips whose immediate publish attempt failed or was interrupted."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text("""
            SELECT c.task_id, c.id FROM generated_clips c
            JOIN tasks t ON t.id = c.task_id
            JOIN live_sessions l ON l.id = t.source_metadata->>'live_session_id'
            WHERE l.youtube_account_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM social_posts p WHERE p.clip_id = c.id AND p.social_account_id = l.youtube_account_id
              )
            ORDER BY c.created_at DESC LIMIT 30
        """))).all()
    for task_id, clip_id in rows:
        await publish_ready_clip(task_id, clip_id)
    return {"checked": len(rows)}
