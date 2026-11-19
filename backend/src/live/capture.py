"""Dedicated long-running live recorder, separate from the render queue."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ..config import Config
from ..database import AsyncSessionLocal
from ..models import LiveSession
from ..repositories.upload_repository import claim_stored_upload
from ..services.billing_service import BillingService, BillingLimitExceeded
from ..services.task_service import TaskService
from ..workers.job_queue import DuplicateJobError, JobQueue
from ..media_storage import MediaStorage
from ..media.ffmpeg import ffprobe_duration
from ..media.upload_ingest import persist_upload_after_response
from ..utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def _update(session_id: str, **fields) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(LiveSession, session_id)
        if row:
            for name, value in fields.items():
                setattr(row, name, value)
            await db.commit()


async def _stop_requested(session_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        row = await db.get(LiveSession, session_id)
        return row is None or row.stop_requested


WINDOW_SECONDS = 300


async def _finalize(session_id: str, source: Path, config: Config, start: int = 0, end: int | None = None) -> None:
    from ..media.ffmpeg import ffprobe_duration
    from ..workflows.durations import MIN_SOURCE_SECONDS

    source_duration = await run_in_thread(ffprobe_duration, source)
    duration = (min(source_duration, end) - start) if end is not None else source_duration - start
    if not duration or duration < MIN_SOURCE_SECONDS:
        raise ValueError("Recording is too short to make clips")
    if duration > config.max_video_duration:
        raise ValueError("Recording exceeded the video duration limit")
    destination = Path(config.temp_dir) / "uploads" / f"{session_id}-{start}.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)
    converter = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(start), "-i", str(source),
        "-t", str(duration), "-c", "copy", "-movflags", "+faststart", str(destination),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, errors = await converter.communicate()
    if converter.returncode or not destination.is_file():
        raise ValueError(f"Could not finalize recording: {errors.decode(errors='replace')[-200:]}")
    if destination.stat().st_size > config.max_video_upload_bytes:
        destination.unlink(missing_ok=True)
        raise ValueError("Recording exceeded the upload size limit")
    video_path = f"upload://{destination.name}"
    async with AsyncSessionLocal() as db:
        row = await db.get(LiveSession, session_id)
        if not row:
            return
        digest = await run_in_thread(_file_digest, destination)
        video_path, created = await claim_stored_upload(
            db, row.user_id, destination.stat().st_size, digest, video_path,
            f"live:{session_id}:{start}", config,
        )
        if created and MediaStorage().enabled:
            await persist_upload_after_response(video_path)
        try:
            await BillingService(db).assert_can_create_task(row.user_id, duration)
        except BillingLimitExceeded as exc:
            raise ValueError(exc.summary.get("reason") or "Generation limit reached") from None
        service = TaskService(db)
        from .ranking import rank_chat_windows

        window_counts = {int(key) - start // 10: int(value) for key, value in row.chat_counts.items() if start <= int(key) * 10 < start + duration}
        peaks = rank_chat_windows(window_counts)
        hints = ", ".join(f"{begin//60}:{begin%60:02d}-{finish//60}:{finish%60:02d}" for begin, finish, _ in peaks)
        instructions = (
            "Prioritize genuinely interesting spoken moments. Chat was unusually active near "
            f"{hints}; consider these moments, but reject weak clips."
        ) if peaks else ""
        task_id = await service.create_task_with_source(
            row.user_id, video_path, title=f"{row.platform.title()} live stream",
            source_duration_seconds=int(duration + .999),
            workflow_options={"instructions": instructions} if instructions else None,
            upload_request_key=f"live:{session_id}:{start}",
        )
        await service.task_repo.save_source_metadata(db, task_id, {
            "live_session_id": session_id, "live_source_url": row.url,
            "chat_counts": window_counts, "live_window_start": start,
        })
        if task_id not in (row.task_ids or []):
            row.task_ids = [*(row.task_ids or []), task_id]
        row.task_id = row.task_id or task_id
        row.processed_until_seconds = max(row.processed_until_seconds, start + int(duration))
        await db.commit()
        try:
            await JobQueue.enqueue_processing_job(
                "process_video_task", "fast", task_id, video_path, "video_url", row.user_id,
                _job_id=f"live-render:{session_id}:{start}",
            )
        except DuplicateJobError:
            pass


async def capture_live_session(ctx: dict, session_id: str) -> dict:
    config = Config()
    async with AsyncSessionLocal() as db:
        row = await db.get(LiveSession, session_id)
        if not row or row.status != "queued":
            return {"status": "skipped"}
        if row.stop_requested:
            row.status = "stopped"
            await db.commit()
            return {"status": "stopped"}
        url = row.url
        platform = row.platform
        row.status = "recording"
        row.started_at = datetime.now(timezone.utc)
        await db.commit()

    directory = Path(config.temp_dir) / "live" / session_id
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "recording.ts"
    process = None
    stopped = False
    chat_stop = asyncio.Event()
    counts: Counter = Counter()
    chat_task = None
    last_checkpoint = time.monotonic()
    next_window = WINDOW_SECONDS
    windows: asyncio.Queue[tuple[int, int] | None] = asyncio.Queue()
    async def process_windows() -> None:
        while window := await windows.get():
            begin, finish = window
            try:
                await _finalize(session_id, source, config, begin, finish)
            except Exception as exc:
                logger.exception("Live window %s %s-%s failed", session_id, begin, finish)
                await _update(session_id, error_message=f"One highlight window failed: {str(exc)[:430]}")
            finally:
                windows.task_done()

    window_task = asyncio.create_task(process_windows())
    try:
        process = await asyncio.create_subprocess_exec(
            "streamlink", "--stdout", url, "720p,best",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        started = time.monotonic()
        last_video = started
        from .chat import collect_counts

        chat_task = asyncio.create_task(collect_counts(platform, url, started, chat_stop, counts))
        with source.open("wb") as output:
            while True:
                try:
                    block = await asyncio.wait_for(process.stdout.read(1024 * 1024), timeout=10)
                except asyncio.TimeoutError:
                    block = None
                if block:
                    output.write(block)
                    last_video = time.monotonic()
                elif block == b"":
                    break
                if time.monotonic() - last_video > (90 if source.stat().st_size else 60):
                    if not source.stat().st_size:
                        raise ValueError("No live video arrived within one minute. Start the broadcast and try again.")
                    break
                if time.monotonic() - started >= config.max_video_duration or source.stat().st_size >= config.max_video_upload_bytes:
                    stopped = True
                    break
                if await _stop_requested(session_id):
                    stopped = True
                    break
                if time.monotonic() - last_checkpoint >= 10:
                    output.flush()
                    os.fsync(output.fileno())
                    await _update(session_id, chat_counts={str(key): value for key, value in counts.items()})
                    last_checkpoint = time.monotonic()
                if time.monotonic() - started >= next_window + 10 and await run_in_thread(ffprobe_duration, source) >= next_window:
                    output.flush()
                    os.fsync(output.fileno())
                    await windows.put((next_window - WINDOW_SECONDS, next_window))
                    next_window += WINDOW_SECONDS
        if process.returncode is None:
            process.terminate()
        await process.wait()
        if not source.is_file() or source.stat().st_size == 0:
            raise ValueError("No live video was recorded. Check that the broadcast is live and public.")
        await _update(session_id, chat_counts={str(key): value for key, value in counts.items()})
        from ..media.ffmpeg import ffprobe_duration

        final_duration = int(await run_in_thread(ffprobe_duration, source))
        tail_start = next_window - WINDOW_SECONDS
        if final_duration - tail_start >= 30:
            await windows.put((tail_start, final_duration))
        await windows.put(None)
        await window_task
        async with AsyncSessionLocal() as db:
            row = await db.get(LiveSession, session_id)
            if not row or not row.task_ids:
                raise ValueError("No highlight windows could be processed")
            row.status = "processing"
            row.ended_at = datetime.now(timezone.utc)
            await db.commit()
        return {"status": "processing", "stopped": stopped}
    except Exception as exc:
        logger.exception("Live capture %s failed", session_id)
        await _update(session_id, status="error", error_message=str(exc)[:500], ended_at=datetime.now(timezone.utc))
        return {"status": "error"}
    finally:
        if not window_task.done():
            window_task.cancel()
            try:
                await window_task
            except asyncio.CancelledError:
                pass
        chat_stop.set()
        if chat_task:
            chat_task.cancel()
            try:
                await chat_task
            except asyncio.CancelledError:
                pass
        if process and process.returncode is None:
            process.kill()
            await process.wait()


async def recover_live_sessions(ctx: dict) -> None:
    """Finalize partial transport streams left by a stopped worker."""
    config = Config()
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(select(LiveSession.id).where(LiveSession.status == "recording"))).scalars().all()
    for session_id in ids:
        source = Path(config.temp_dir) / "live" / session_id / "recording.ts"
        if not source.is_file() or not source.stat().st_size:
            await _update(session_id, status="error", error_message="Recording stopped before any video was saved")
            continue
        try:
            async with AsyncSessionLocal() as db:
                row = await db.get(LiveSession, session_id)
                start = row.processed_until_seconds if row else 0
            await _finalize(session_id, source, config, start)
            await _update(session_id, status="processing", ended_at=datetime.now(timezone.utc))
        except Exception as exc:
            logger.exception("Could not recover live recording %s", session_id)
            await _update(session_id, status="error", error_message=str(exc)[:500])


async def on_live_worker_startup(ctx: dict) -> None:
    await recover_live_sessions(ctx)


class LiveWorkerSettings:
    from arq.connections import RedisSettings

    config = Config()
    queue_name = "supoclip_live"
    functions = [capture_live_session]
    redis_settings = RedisSettings(host=config.redis_host, port=config.redis_port, password=config.redis_password, database=0)
    max_jobs = 1
    max_tries = 1
    job_timeout = 4 * 3600
    on_startup = on_live_worker_startup
