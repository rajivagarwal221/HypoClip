"""Optional live chat counters. Message text and user identities are discarded."""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import time
from collections import Counter
from urllib.parse import parse_qs, urlparse

import requests


async def twitch_counts(channel: str, started: float, stop: asyncio.Event, counts: Counter) -> None:
    username = os.getenv("TWITCH_CHAT_USERNAME", "").strip()
    token = os.getenv("TWITCH_CHAT_TOKEN", "").strip()
    if not username or not token:
        return
    reader, writer = await asyncio.open_connection("irc.chat.twitch.tv", 6697, ssl=ssl.create_default_context())
    try:
        writer.write(f"PASS oauth:{token.removeprefix('oauth:')}\r\nNICK {username}\r\nJOIN #{channel}\r\n".encode())
        await writer.drain()
        while not stop.is_set():
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            if line.startswith(b"PING"):
                writer.write(line.replace(b"PING", b"PONG", 1))
                await writer.drain()
            elif b" PRIVMSG #" in line:
                counts[int((time.monotonic() - started) // 10)] += 1
    finally:
        writer.close()
        await writer.wait_closed()


def _youtube_chat_id(video_id: str, api_key: str) -> str | None:
    response = requests.get("https://www.googleapis.com/youtube/v3/videos", params={
        "part": "liveStreamingDetails", "id": video_id, "key": api_key,
    }, timeout=12)
    response.raise_for_status()
    items = response.json().get("items", [])
    return (items[0].get("liveStreamingDetails") or {}).get("activeLiveChatId") if items else None


def _youtube_messages(chat_id: str, api_key: str, page_token: str | None) -> dict:
    params = {"part": "snippet", "liveChatId": chat_id, "key": api_key, "maxResults": 2000}
    if page_token:
        params["pageToken"] = page_token
    response = requests.get("https://www.googleapis.com/youtube/v3/liveChat/messages", params=params, timeout=15)
    response.raise_for_status()
    return response.json()


async def youtube_counts(video_id: str, started: float, stop: asyncio.Event, counts: Counter) -> None:
    api_key = os.getenv("YOUTUBE_DATA_API_KEY", "").strip()
    if not api_key:
        return
    chat_id = await asyncio.to_thread(_youtube_chat_id, video_id, api_key)
    if not chat_id:
        return
    page_token = None
    first_page = True
    seen: set[str] = set()
    while not stop.is_set():
        data = await asyncio.to_thread(_youtube_messages, chat_id, api_key, page_token)
        for item in data.get("items", []):
            message_id = item.get("id")
            if first_page or not message_id or message_id in seen:
                continue
            seen.add(message_id)
            counts[int((time.monotonic() - started) // 10)] += 1
        first_page = False
        page_token = data.get("nextPageToken")
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(1, data.get("pollingIntervalMillis", 10000) / 1000))
        except asyncio.TimeoutError:
            pass


async def collect_counts(platform: str, url: str, started: float, stop: asyncio.Event, counts: Counter) -> None:
    try:
        parsed = urlparse(url)
        if platform == "twitch":
            await twitch_counts(parsed.path.strip("/").lower(), started, stop, counts)
        elif platform == "youtube":
            from ..youtube_utils import get_youtube_video_id

            video_id = get_youtube_video_id(url)
            if video_id:
                await youtube_counts(video_id, started, stop, counts)
        # Kick's public chat transport is not a supported stable API here.
    except asyncio.CancelledError:
        raise
    except Exception:
        # Chat is an optional signal. Capture continues and transcript selection wins.
        import logging

        logging.getLogger(__name__).warning("Live chat unavailable for %s", platform, exc_info=True)
