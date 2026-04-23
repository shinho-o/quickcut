"""
영상 처리 코어 — Whisper 전사 + ffmpeg 트림/인코딩/자막 번인/컨캣.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def probe_duration(video: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip() or 0.0)


def probe_resolution(video: Path) -> tuple[int, int]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(r.stdout)
    s = data["streams"][0]
    return int(s["width"]), int(s["height"])


def transcribe(video: Path, model_name: str = "small", lang: str = "ko") -> list[dict]:
    """Whisper 로 세그먼트 전사."""
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segs_iter, _ = model.transcribe(str(video), language=lang, beam_size=1, vad_filter=True)
    return [
        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
        for s in segs_iter
    ]


def run(cmd: list[str], cwd: str | None = None) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg exit {r.returncode}: {r.stderr[-500:]}")
    return r.stdout


def find_korean_font() -> str | None:
    """한국어 가능 폰트 탐색 (OS 독립)."""
    candidates = [
        r"C:\Windows\Fonts\malgun.ttf",
        r"C:\Windows\Fonts\NanumGothic.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def _escape_drawtext(text: str) -> str:
    return (text
            .replace("\\", r"\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
            .replace(",", r"\,")
            .replace("%", r"\%")
            .replace("\n", " "))


# ───────── 스타일 프리셋 ─────────

STYLE_PRESETS: dict[str, dict] = {
    "minimal": {
        "label": "미니멀",
        "font_size": 44,
        "color": "white", "border": 2, "border_color": "black",
        "box": 0, "box_color": None, "box_opacity": 0,
        "y": "h-th-80",
    },
    "bold": {
        "label": "굵게",
        "font_size": 56,
        "color": "white", "border": 4, "border_color": "black",
        "box": 0, "box_color": None, "box_opacity": 0,
        "y": "h-th-120",
    },
    "boxed": {
        "label": "박스",
        "font_size": 40,
        "color": "white", "border": 0, "border_color": None,
        "box": 1, "box_color": "black", "box_opacity": 0.55,
        "y": "h-th-100",
    },
    "top": {
        "label": "상단",
        "font_size": 42,
        "color": "white", "border": 3, "border_color": "black",
        "box": 0, "box_color": None, "box_opacity": 0,
        "y": 80,
    },
}


def build_caption_filter(segments: list[dict], preset_name: str,
                         font_path: str | None = None) -> str:
    """drawtext 필터 체인 — subtitles 필터의 Windows 경로 문제 우회."""
    preset = STYLE_PRESETS.get(preset_name, STYLE_PRESETS["minimal"])

    ff_font = None
    if font_path:
        ff_font = font_path.replace("\\", "/").replace(":", r"\:")

    filters = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        esc = _escape_drawtext(text)
        parts = [
            f"drawtext=text='{esc}'",
            f"fontsize={preset['font_size']}",
            f"fontcolor={preset['color']}",
            "x=(w-text_w)/2",
            f"y={preset['y']}",
            f"enable='between(t\\,{seg['start']:.2f}\\,{seg['end']:.2f})'",
        ]
        if preset.get("border", 0):
            parts.append(f"borderw={preset['border']}")
            parts.append(f"bordercolor={preset['border_color']}")
        if preset.get("box", 0):
            parts.append("box=1")
            parts.append(f"boxcolor={preset['box_color']}@{preset['box_opacity']}")
            parts.append("boxborderw=12")
        if ff_font:
            parts.append(f"fontfile='{ff_font}'")
        filters.append(":".join(parts))
    return ",".join(filters) if filters else ""


# ───────── 클립 처리 ─────────

def trim_clip(src: Path, out: Path, start: float, end: float):
    """재인코딩해서 키프레임 의존성 제거 (정확한 컷)."""
    run([
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", str(src),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(out),
    ])


def concat_clips(clips: list[Path], out: Path):
    """여러 mp4 를 concat demuxer 로 이어붙임 (전부 같은 인코딩 조건).
    단일 클립이면 바로 복사.
    """
    if len(clips) == 1:
        shutil.copy(clips[0], out)
        return

    tmpdir = Path(tempfile.mkdtemp(prefix="qc_concat_"))
    listing = tmpdir / "list.txt"
    listing.write_text(
        "\n".join(f"file '{c.as_posix()}'" for c in clips),
        encoding="utf-8",
    )
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(listing), "-c", "copy",
        "-movflags", "+faststart",
        str(out),
    ])


def apply_effects(src: Path, out: Path, segments: list[dict] | None,
                  preset_name: str, vertical: bool):
    """자막 번인 + 선택적 9:16 세로 변환.
    vertical=True 이면 1080x1920 로 스케일+패드 (레터박스).
    """
    vf_parts = []

    if segments:
        font = find_korean_font()
        caption_vf = build_caption_filter(segments, preset_name, font)
        if caption_vf:
            vf_parts.append(caption_vf)

    if vertical:
        # 9:16 세로. 원본 비율 유지하며 중앙 배치, 빈 영역 블랙.
        vf_parts.append(
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
        )

    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(out),
    ]
    run(cmd)
