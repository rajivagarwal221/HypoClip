"""Rank chat bursts without retaining message content."""

from __future__ import annotations

from collections import Counter
from statistics import median


def ten_second_counts(timestamps: list[float], started_at: float) -> dict[int, int]:
    """Map elapsed ten-second bucket numbers to message counts."""
    return dict(Counter(int((ts - started_at) // 10) for ts in timestamps if ts >= started_at))


def rank_chat_windows(
    counts: dict[int, int], *, window_seconds: int = 60, max_windows: int = 8
) -> list[tuple[int, int, float]]:
    """Return nonoverlapping (start, end, score) windows in stream seconds."""
    if not counts or max_windows <= 0:
        return []
    width = max(1, (window_seconds + 9) // 10)
    last = max(counts)
    baseline = max(1.0, float(median(counts.get(i, 0) for i in range(last + 1))))
    candidates = []
    for start in range(last + 1):
        total = sum(counts.get(i, 0) for i in range(start, min(start + width, last + 1)))
        if total:
            candidates.append((start * 10, (start + width) * 10, total / (baseline * width)))
    chosen: list[tuple[int, int, float]] = []
    for candidate in sorted(candidates, key=lambda item: (-item[2], item[0])):
        if all(candidate[1] <= previous[0] or candidate[0] >= previous[1] for previous in chosen):
            chosen.append(candidate)
            if len(chosen) == max_windows:
                break
    return sorted(chosen)
