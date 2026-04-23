"""
무음 구간 감지 — librosa 기반 RMS 임계값.
Whisper 세그먼트 사이의 긴 공백을 찾아 "컷 후보" 로 반환.
"""
from __future__ import annotations

from pathlib import Path


def detect_silence_ranges(
    video: Path,
    segments: list[dict],
    min_gap_sec: float = 1.2,
    rms_threshold: float = 0.015,
) -> list[dict]:
    """
    음성 세그먼트 간 공백 중 RMS 가 낮은 구간을 "무음 후보" 로 반환.

    출력:
        [{"start": float, "end": float, "duration": float, "suggest_skip": bool}, ...]
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(str(video), sr=22050, mono=True)
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    total = float(times[-1]) if len(times) else 0.0

    # 세그먼트 구간 외 영역이 "음성 없음"
    voiced = sorted([(s["start"], s["end"]) for s in segments], key=lambda x: x[0])

    gaps: list[tuple[float, float]] = []
    cur = 0.0
    for a, b in voiced:
        if a - cur > 0.01:
            gaps.append((cur, a))
        cur = max(cur, b)
    if total - cur > 0.01:
        gaps.append((cur, total))

    ranges = []
    for a, b in gaps:
        if b - a < min_gap_sec:
            continue
        # 해당 구간의 평균 RMS
        mask = (times >= a) & (times <= b)
        avg_rms = float(rms[mask].mean()) if mask.any() else 0.0
        ranges.append({
            "start": round(a, 2),
            "end": round(b, 2),
            "duration": round(b - a, 2),
            "rms": round(avg_rms, 4),
            "suggest_skip": avg_rms < rms_threshold,
        })
    return ranges


def apply_skips(
    clip_duration: float,
    segments: list[dict],
    skip_ranges: list[dict],
) -> tuple[list[tuple[float, float]], list[dict]]:
    """
    스킵할 구간을 빼고 남길 구간들을 반환.

    반환:
        keep_ranges: [(start, end), ...] — 원본 타임라인 기준
        shifted_segments: [{start, end, text}, ...] — 컷 적용 후 타임라인 기준
    """
    # 스킵 구간 정렬·병합
    skips = sorted([(s["start"], s["end"]) for s in skip_ranges
                    if s.get("suggest_skip", True)])
    merged: list[list[float]] = []
    for a, b in skips:
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    # keep 구간 = [0, total] - skips
    keep: list[tuple[float, float]] = []
    cur = 0.0
    for a, b in merged:
        if a > cur + 0.05:
            keep.append((cur, a))
        cur = max(cur, b)
    if clip_duration - cur > 0.05:
        keep.append((cur, clip_duration))
    if not keep:
        keep = [(0, clip_duration)]

    # 세그먼트 타임 재계산
    shifted = []
    offset_table = []
    running = 0.0
    for a, b in keep:
        offset_table.append((a, b, running - a))
        running += b - a

    for seg in segments:
        for a, b, shift in offset_table:
            if seg["start"] >= a and seg["end"] <= b:
                shifted.append({
                    "start": round(seg["start"] + shift, 2),
                    "end": round(seg["end"] + shift, 2),
                    "text": seg["text"],
                })
                break
    return keep, shifted
