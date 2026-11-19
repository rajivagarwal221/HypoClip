# Live Stream Clipping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept one Twitch, YouTube, or Kick live URL, create clips during the broadcast, and upload each ready clip publicly to the selected connected YouTube account.

**Architecture:** A dedicated live-capture worker records the broadcast and optional chat activity. Five-minute video windows are handed to the existing video pipeline while capture continues; each ready clip uses the existing social publisher and a retry job to upload publicly to the selected YouTube account.

**Tech Stack:** FastAPI, SQLAlchemy/Postgres, arq/Redis, Streamlink, FFmpeg, Next.js.

**Spec:** Updated by the user: per-URL submission, all three platforms, clips while live, public automatic YouTube publishing, transcript fallback when chat is unavailable.

## Global Constraints

- Do not change existing completed-video submissions.
- Bound recording by existing per-user duration and storage limits; expose stop and failure states.
- Keep video and chat capture independent; chat outages must not lose the video.
- Require a connected YouTube account before starting a public auto-upload session.
- Never collect chat message bodies when counts suffice.
- Avoid new paid services.

## Review Focus

- Duplicate live URL submissions must not create duplicate recordings.
- A stopped stream must finalize successfully when usable segments exist.
- Recorder crashes must preserve completed segments and resume or report a recoverable failure.
- Missing chat credentials must select transcript fallback without failing capture.
- A stream that exceeds the allowed duration must stop and report the limit.

---

### Task 1: Live session persistence and API

**Files:** `backend/src/models.py`, `backend/src/migrations/sql/20261119_0001_live_sessions.sql`, `backend/src/api/routes/live_sessions.py`, `backend/src/main.py`, `backend/tests/test_live_sessions.py`

**Interfaces:** `POST /live-sessions`, `GET /live-sessions`, `GET /live-sessions/{id}`, `POST /live-sessions/{id}/stop`. Store owner, platform, URL, status, timestamps, segment manifest, chat counts, task id, and bounded error message.

- [ ] Test owner-only create, list, read and stop; duplicate submission; malformed or nonlive URL.
- [ ] Implement schema and routes using existing signed auth dependency.
- [ ] Run targeted API tests.

### Task 2: Recoverable recording

**Files:** `backend/src/live/recorder.py`, `backend/src/live/jobs.py`, `backend/src/workers/run_workers.py`, `backend/tests/test_live_recorder.py`

**Interfaces:** `capture(session_id)` and `finalize(session_id)`; launch Streamlink without shell interpolation; save bounded, independently usable segments; recognize end, stop, restart and limit.

- [ ] Test process failure, completed segments, stream end, duration limit and cancellation.
- [ ] Implement capture job on a dedicated queue separate from rendering.
- [ ] Run targeted recorder tests.

### Task 3: Chat activity and ranking

**Files:** `backend/src/live/chat.py`, `backend/src/live/ranking.py`, `backend/tests/test_live_ranking.py`

**Interfaces:** timestamped 10-second counts; ranked nonoverlapping candidate windows. Use authorized Twitch and YouTube adapters when configured; Kick adapter only if a stable supported API is verified, otherwise fallback.

- [ ] Test sparse chat, spam bursts, clock drift, missing chat and repeated spikes.
- [ ] Implement activity capture and combined chat/transcript ranking.
- [ ] Run targeted ranking tests.

### Task 4: Existing pipeline handoff

**Files:** `backend/src/live/finalize.py`, `backend/src/services/task_service.py`, `backend/tests/test_live_finalize.py`

**Interfaces:** finalized recording becomes a metered source task, storing origin URL and chat hints in task metadata. Reuse existing renderer and completion notification.

- [ ] Test recording-to-task handoff, idempotent retry, storage availability and failed generation.
- [ ] Implement handoff and queue processing exactly once.
- [ ] Run targeted pipeline tests.

### Task 5: Frontend flow and setup

**Files:** `frontend/src/components/home-app.tsx`, `frontend/src/app/api/live-sessions/*`, `frontend/src/lib/video-upload.ts`, `backend/Dockerfile`, `backend/README.md`, frontend tests.

**Interfaces:** Separate live submission; session status, stop, clips link; explain chat fallback and configuration.

- [ ] Test live URL submission, status, stop, error and finished task link.
- [ ] Implement signed proxy routes and UI without changing VOD handling.
- [ ] Run frontend and backend targeted tests plus typecheck.
