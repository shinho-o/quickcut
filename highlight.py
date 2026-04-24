"""
자동 하이라이트 선택.

여러 영상에서 "재밌을 것 같은 구간" 을 점수화해 목표 시간만큼 뽑아낸다.
점수 = 말의 밀도 + 오디오 에너지 피크 + 길이 보너스.
"""
from __future__ import annotations

from pathlib import Path


def _segment_energy(video: Path, segments: list[dict]) -> list[float]:
    """각 segment 의 평균 RMS. librosa 로 오디오 로드."""
    import librosa
    import numpy as np

    y, sr = librosa.load(str(video), sr=22050, mono=True)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=512)

    out = []
    for s in segments:
        mask = (times >= s["start"]) & (times <= s["end"])
        out.append(float(rms[mask].mean()) if mask.any() else 0.0)
    return out


def score_segments(video: Path, segments: list[dict]) -> list[dict]:
    """
    segments (whisper 출력) 에 score 필드를 더해 반환.
      - density: 초당 글자수 (빠른 말·정보 밀도)
      - energy: 평균 RMS (감정 하이라이트)
      - length_bonus: 2~6초 구간에 가점 (짧아서 버리기 쉬운 단편 억제)
    """
    if not segments:
        return []

    try:
        energies = _segment_energy(video, segments)
    except Exception:
        energies = [0.0] * len(segments)

    enriched = []
    max_energy = max(energies) or 1.0
    for seg, e in zip(segments, energies):
        dur = max(0.1, seg["end"] - seg["start"])
        density = len(seg.get("text", "")) / dur
        length_bonus = 1.0 if 2.0 <= dur <= 6.0 else 0.7
        score = (
            0.55 * min(density / 6.0, 1.0)   # 초당 6자를 만점으로 정규화
            + 0.35 * (e / max_energy)
            + 0.10 * length_bonus
        )
        enriched.append({**seg, "score": round(score, 4),
                         "density": round(density, 2),
                         "energy": round(e, 4)})
    return enriched


def pick_top(segments: list[dict], target_duration: float,
             keep_order: bool = True) -> list[dict]:
    """
    점수 내림차순으로 누적 선택해 target_duration 에 가깝게 채우고,
    keep_order=True 면 원본 타임라인 순서로 재정렬.
    """
    ranked = sorted(segments, key=lambda s: s["score"], reverse=True)
    picked = []
    total = 0.0
    for s in ranked:
        d = s["end"] - s["start"]
        if total + d > target_duration * 1.15:  # 최대 15% 초과 허용
            continue
        picked.append(s)
        total += d
        if total >= target_duration:
            break

    if keep_order:
        picked.sort(key=lambda s: s["start"])
    return picked
