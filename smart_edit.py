"""
똑똑한 자동 편집 — 단어 단위 점프컷 + 간투어 제거 + (옵션) Claude 분석.

입력: Whisper 의 word_timestamps 결과
출력:
    keep_ranges: [(start, end), ...] — 원본에서 유지할 시간 범위 리스트
    kept_words:  [{start, end, word}] — 유지된 단어 목록 (최종 타임라인 기준)
"""
from __future__ import annotations

import os
from pathlib import Path


# 한국어 간투어 · 필러 단어 (공백 제거 후 비교)
FILLER_WORDS = {
    "음", "어", "아", "으음", "어어", "아니", "뭐",
    "그", "그러니까", "그니까", "그러면", "근데",
    "인제", "이제", "뭐랄까", "뭐라고해야되지",
    "좀", "막", "진짜", "그냥",   # (유의: 문맥따라 의도적 — 옵션)
}
# '좀', '막', '진짜', '그냥' 은 의미 있을 때 많아 기본 유지. 공격적 제거 모드만 제거.

FILLER_AGGRESSIVE_EXTRA = {"좀", "막", "진짜", "그냥", "뭐", "이제"}


def _norm(word: str) -> str:
    return word.strip().rstrip(".,!?~…").strip()


def build_keep_plan(
    segments: list[dict],
    *,
    max_gap_sec: float = 0.4,
    remove_fillers: bool = True,
    aggressive_fillers: bool = False,
    pad_sec: float = 0.08,
) -> dict:
    """
    segments (word_timestamps 포함) → 유지할 구간과 단어 목록.

    규칙:
      1. 단어 사이 gap 이 max_gap_sec 초과면 그 gap 을 통째로 삭제
      2. remove_fillers=True 면 간투어도 통째로 삭제
      3. 남은 단어들을 인접한 것끼리 묶어 keep_ranges 생성
      4. 각 유지 구간 앞뒤에 pad_sec 여유 (자연스러움)
    """
    # 모든 단어 flat 화
    all_words: list[dict] = []
    for s in segments:
        for w in s.get("words", []):
            w2 = dict(w)
            w2["_text"] = _norm(w["word"])
            all_words.append(w2)

    if not all_words:
        return {"keep_ranges": [], "kept_words": [], "total_saved": 0.0}

    filler_set = set(FILLER_WORDS)
    if aggressive_fillers:
        filler_set |= FILLER_AGGRESSIVE_EXTRA

    kept: list[dict] = []
    dropped_filler = 0
    for i, w in enumerate(all_words):
        txt = w["_text"]
        is_filler = remove_fillers and txt in filler_set
        if is_filler:
            dropped_filler += 1
            continue

        # 긴 gap 차단: 이전 유지 단어와 시작 사이 gap 이 max_gap 초과면
        # 이 단어를 새 구간 시작으로 찍되, keep_ranges 에선 gap 이 빠짐
        kept.append(w)

    if not kept:
        return {"keep_ranges": [], "kept_words": [],
                "total_saved": 0.0, "fillers_removed": dropped_filler}

    # kept → 연속 구간으로 묶기. 유지 단어들 사이 gap > max_gap 이면 범위 분할.
    ranges: list[list[float]] = []
    cur = [kept[0]["start"], kept[0]["end"]]
    for prev, w in zip(kept, kept[1:]):
        gap = w["start"] - prev["end"]
        if gap > max_gap_sec:
            ranges.append(cur)
            cur = [w["start"], w["end"]]
        else:
            cur[1] = max(cur[1], w["end"])
    ranges.append(cur)

    # 패딩
    keep_ranges = [
        (max(0.0, r[0] - pad_sec), r[1] + pad_sec) for r in ranges
    ]

    # 유지 단어를 최종 타임라인 기준으로 재계산
    kept_words_out: list[dict] = []
    offset = 0.0
    pointer = 0
    for r_start, r_end in keep_ranges:
        # 이 범위 안의 단어들 찾기
        while pointer < len(kept) and kept[pointer]["start"] < r_start:
            pointer += 1
        while pointer < len(kept) and kept[pointer]["end"] <= r_end + 0.05:
            w = kept[pointer]
            kept_words_out.append({
                "start": round(w["start"] - r_start + offset, 2),
                "end": round(w["end"] - r_start + offset, 2),
                "word": w["word"].strip(),
            })
            pointer += 1
        offset += (r_end - r_start)

    total_original = (all_words[-1]["end"] - all_words[0]["start"])
    total_kept = sum(r[1] - r[0] for r in keep_ranges)
    return {
        "keep_ranges": [(round(a, 2), round(b, 2)) for a, b in keep_ranges],
        "kept_words": kept_words_out,
        "total_original": round(total_original, 2),
        "total_kept": round(total_kept, 2),
        "total_saved": round(total_original - total_kept, 2),
        "fillers_removed": dropped_filler,
    }


def words_to_segments(words: list[dict], group_ms: int = 1200) -> list[dict]:
    """단어 목록 → 자막 세그먼트 (group_ms 보다 짧게 묶어 한 줄로).
    드로우텍스트에 너무 많은 세그먼트가 들어가지 않도록 합친다."""
    out: list[dict] = []
    cur = None
    for w in words:
        if cur is None:
            cur = {"start": w["start"], "end": w["end"], "text": w["word"]}
            continue
        # 동일 세그먼트에 합칠지
        if (w["end"] - cur["start"]) * 1000 <= group_ms:
            cur["text"] = (cur["text"] + " " + w["word"]).strip()
            cur["end"] = w["end"]
        else:
            out.append(cur)
            cur = {"start": w["start"], "end": w["end"], "text": w["word"]}
    if cur:
        out.append(cur)
    return out


# ───── Claude 분석 (선택) ─────

def claude_punchup(segments: list[dict], model: str = "claude-sonnet-4-6") -> dict | None:
    """Claude 가 자막을 읽고 훅·키 모먼트·재작성 제안.

    실패하거나 키 없으면 None 반환 (편집 파이프라인은 계속 진행).
    """
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key or not segments:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    # 자막 텍스트 조립 (토큰 절약)
    script_lines = [
        f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}"
        for s in segments if s.get("text")
    ]
    if not script_lines:
        return None
    script = "\n".join(script_lines[:200])  # 길어도 200줄

    prompt = f"""다음은 브이로그 영상의 자동 생성 자막입니다. 쇼츠/릴스 바이럴용으로 편집할 때
어떻게 손대면 좋을지 다음 JSON 형식으로 답해주세요. 반드시 순수 JSON 만:

{{
  "hook_start": <가장 강한 오프닝이 되는 시작 초>,
  "hook_end": <끝 초>,
  "hook_reason": "<왜 훅인지 한 줄>",
  "key_moments": [
    {{"start": <초>, "end": <초>, "reason": "<왜 임팩트 있는지>"}}
  ],
  "punched_captions": [
    {{"start": <초>, "end": <초>,
      "original": "<원 자막>",
      "punched": "<짧고 임팩트있게 다듬은 자막 (15자 이내 선호)>"}}
  ]
}}

자막:
{script}
"""

    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        # JSON 추출
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        import json as _json
        return _json.loads(text)
    except Exception:
        return None
