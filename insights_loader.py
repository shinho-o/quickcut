"""
벳슼 vlog 파이프라인이 만들어준 편집 지침서(md)를 읽어 QuickCut 의
자막 스타일 프리셋으로 변환한다.

지침서 스키마 (YAML frontmatter):
  date, video_count, cut_interval_mean, cut_interval_median,
  chars_per_sec_mean, bpm_mean, top_colors

이 값을 기반으로 가변 preset 을 생성:
  - top_colors 첫 번째 밝은 색을 강조 색으로 (검정 계열 제외)
  - chars_per_sec 가 높을수록 폰트 작게 (빠른 읽기)
  - 하단 여백 80% 지점 고정
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path


DATA_INSIGHTS = Path(__file__).parent / "data" / "insights"


def _parse_md(path: Path) -> dict:
    """YAML frontmatter 와 body 를 분리 · 파싱."""
    txt = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", txt, re.DOTALL)
    fm, body = {}, txt
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            # 배열 (예: top_colors: ['#000000', ...])
            if v.startswith("[") and v.endswith("]"):
                v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
            else:
                try:
                    v = float(v) if "." in v else int(v)
                except ValueError:
                    pass
            fm[k] = v
    return {"frontmatter": fm, "body": body, "path": path}


def latest() -> dict | None:
    """가장 최근 날짜 md 하나 반환. 없으면 None."""
    if not DATA_INSIGHTS.exists():
        return None
    mds = sorted(DATA_INSIGHTS.glob("*.md"))
    if not mds:
        return None
    return _parse_md(mds[-1])


def _pick_accent_color(colors: list[str]) -> str:
    """어두운 색(#000000 계열) 건너뛰고 무난히 보이는 강조색 찾기."""
    for c in colors:
        if not isinstance(c, str) or not c.startswith("#") or len(c) != 7:
            continue
        r = int(c[1:3], 16)
        g = int(c[3:5], 16)
        b = int(c[5:7], 16)
        # 밝기 가중 (ITU-R BT.601)
        brightness = 0.299 * r + 0.587 * g + 0.114 * b
        if brightness > 80:  # 너무 어둡지 않은 색
            return c
    return "#FFD080"  # 기본 따뜻한 골드


def build_preset() -> dict | None:
    """최근 지침서 → STYLE_PRESETS 포맷의 dict. 없으면 None."""
    ins = latest()
    if not ins:
        return None

    fm = ins["frontmatter"]
    cps = float(fm.get("chars_per_sec_mean") or 5.5)
    accent = _pick_accent_color(fm.get("top_colors") or [])

    # chars/sec 가 빠르면(> 6) 자막을 조금 더 작게, 느리면(< 5) 크게
    if cps > 6.5:
        fs = 42
    elif cps < 5.0:
        fs = 54
    else:
        fs = 48

    return {
        "label": f"이번주 지침 ({fm.get('date', '')})",
        "font_size": fs,
        "color": "white",
        "border": 3,
        "border_color": "black",
        "box": 0,
        "box_color": None,
        "box_opacity": 0,
        # 화면 하단 80% 지점 (일반 브이로그 관행)
        "y": "h*0.80",
        "_meta": {
            "source": str(ins["path"].name),
            "date": fm.get("date"),
            "accent_color": accent,
            "cps": cps,
            "bpm": fm.get("bpm_mean"),
        },
    }


if __name__ == "__main__":
    import json
    p = build_preset()
    print(json.dumps(p, ensure_ascii=False, indent=2) if p else "지침서 없음")
