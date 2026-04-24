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


def probe_codec(video: Path) -> str:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True,
    )
    return r.stdout.strip().lower()


def make_browser_preview(src: Path, preview_out: Path) -> bool:
    """HEVC·AV1 등 브라우저 비호환 코덱이면 H.264 로 트랜스코딩.
    이미 h264 면 파일만 복사(재인코딩 없음)로 빠르게 끝냄.
    """
    codec = probe_codec(src)
    if codec in {"h264", "avc", "avc1"}:
        try:
            import shutil
            shutil.copy(src, preview_out)
            return True
        except Exception:
            pass

    # H.264 + AAC 저용량 프리뷰 (웹 재생용 — export 에선 원본 사용)
    try:
        run([
            "ffmpeg", "-y", "-i", str(src),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-vf", "scale='min(1280,iw)':-2",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(preview_out),
        ])
        return True
    except Exception:
        return False


import threading as _th
_WHISPER_CACHE: dict = {}
_WHISPER_LOCK = _th.Lock()


def _get_whisper(model_name: str):
    from faster_whisper import WhisperModel
    with _WHISPER_LOCK:
        if model_name not in _WHISPER_CACHE:
            _WHISPER_CACHE[model_name] = WhisperModel(
                model_name, device="cpu", compute_type="int8")
        return _WHISPER_CACHE[model_name]


def transcribe(video: Path, model_name: str = "small", lang: str = "ko",
               word_timestamps: bool = False) -> list[dict]:
    """Whisper 전사. word_timestamps=True 면 세그먼트에 `words` 배열도 포함.

    반환:
      [{start, end, text, [words: [{start, end, word}]]}, ...]
    """
    model = _get_whisper(model_name)
    segs_iter, _ = model.transcribe(
        str(video), language=lang, beam_size=1, vad_filter=True,
        word_timestamps=word_timestamps,
    )
    out = []
    for s in segs_iter:
        entry = {
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": s.text.strip(),
        }
        if word_timestamps and getattr(s, "words", None):
            entry["words"] = [
                {"start": round(w.start, 2), "end": round(w.end, 2),
                 "word": w.word.strip()}
                for w in s.words if w.word.strip()
            ]
        out.append(entry)
    return out


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


def get_presets() -> dict:
    """기본 프리셋 + (지침서 md 가 있으면) 동적 '이번주 지침' 프리셋."""
    presets = dict(STYLE_PRESETS)
    try:
        import insights_loader
        p = insights_loader.build_preset()
        if p:
            presets["insight"] = p
    except Exception:
        pass
    return presets


def build_caption_filter(segments: list[dict], preset_name: str,
                         font_path: str | None = None,
                         word_highlight: bool = False,
                         highlight_color: str = "#FFD080") -> str:
    """drawtext 필터 체인 — subtitles 필터의 Windows 경로 문제 우회.

    word_highlight=True 이고 세그먼트에 words 가 있으면 단어별로 두 개의
    drawtext 를 발행 (기본색 + 현재 단어만 강조색).
    """
    all_presets = get_presets()
    preset = all_presets.get(preset_name, all_presets["minimal"])

    ff_font = None
    if font_path:
        ff_font = font_path.replace("\\", "/").replace(":", r"\:")

    def base_parts(text_esc: str, start: float, end: float, color: str | None = None):
        parts = [
            f"drawtext=text='{text_esc}'",
            f"fontsize={preset['font_size']}",
            f"fontcolor={color or preset['color']}",
            "x=(w-text_w)/2",
            f"y={preset['y']}",
            f"enable='between(t\\,{start:.2f}\\,{end:.2f})'",
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
        return ":".join(parts)

    # 강조색 보정 (#RRGGBB → ffmpeg 0xRRGGBB or name)
    hi = highlight_color or "#FFD080"
    if hi.startswith("#"):
        hi_ff = "0x" + hi[1:]
    else:
        hi_ff = hi

    filters = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        words = seg.get("words")

        if word_highlight and words:
            # Opus Clip 스타일: 한 단어씩 팝업으로만 표시 (전체 문장은 안 그림)
            for w in words:
                wt = w.get("word", "").strip()
                if not wt:
                    continue
                esc_w = _escape_drawtext(wt)
                filters.append(
                    base_parts(esc_w, float(w["start"]), float(w["end"]), color=hi_ff)
                )
        else:
            # 기본: 세그먼트 전체 문장을 구간 동안 표시
            esc = _escape_drawtext(text)
            filters.append(base_parts(esc, seg["start"], seg["end"]))
    return ",".join(filters) if filters else ""


# ───────── 클립 처리 ─────────

def trim_clip(src: Path, out: Path, start: float, end: float):
    """정확한 프레임 컷 + 해상도/fps/포맷 통일 (concat 호환).
    lanczos 고급 리스케일링으로 1080p 다운스케일 시 샤프 유지.
    """
    run([
        "ffmpeg", "-y",
        "-i", str(src),
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-vf", (
            "scale=w=1920:h=1080:force_original_aspect_ratio=decrease:flags=lanczos,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,"
            "fps=30,format=yuv420p"
        ),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(out),
    ])


def _concat_list_escape(path: Path) -> str:
    """concat demuxer 의 file 경로 — 싱글쿼트만 탈출."""
    return path.as_posix().replace("'", r"'\''")


def concat_clips(clips: list[Path], out: Path):
    """여러 mp4 를 concat FILTER 로 이어붙임 (안정성 우선).
    단일 클립이면 복사. 빈 리스트면 에러.
    """
    if not clips:
        raise RuntimeError(
            "편집할 구간이 없습니다. 자막·트림·하이라이트 설정을 확인해주세요."
        )
    if len(clips) == 1:
        shutil.copy(clips[0], out)
        return

    n = len(clips)
    cmd = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", str(c)]

    # [0:v][0:a][1:v][1:a]...concat=n=N:v=1:a=1[vo][ao]
    streams = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    filter_cx = f"{streams}concat=n={n}:v=1:a=1[vo][ao]"

    cmd += [
        "-filter_complex", filter_cx,
        "-map", "[vo]", "-map", "[ao]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    run(cmd)


def apply_effects(src: Path, out: Path, segments: list[dict] | None,
                  preset_name: str, vertical: bool,
                  smart_crop_plan: list[dict] | None = None,
                  word_highlight: bool = False,
                  highlight_color: str = "#FFD080"):
    """자막 번인 + 선택적 9:16 세로 변환."""
    if vertical and smart_crop_plan:
        _apply_smart_crop(src, out, segments, preset_name, smart_crop_plan,
                          word_highlight=word_highlight,
                          highlight_color=highlight_color)
        return

    vf_parts = []
    if segments:
        font = find_korean_font()
        caption_vf = build_caption_filter(
            segments, preset_name, font,
            word_highlight=word_highlight,
            highlight_color=highlight_color,
        )
        if caption_vf:
            vf_parts.append(caption_vf)
    if vertical:
        vf_parts.append(
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
        )

    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    run(cmd)


def _apply_smart_crop(src: Path, out: Path, segments: list[dict] | None,
                      preset_name: str, plan: list[dict],
                      word_highlight: bool = False,
                      highlight_color: str = "#FFD080"):
    """얼굴 추적 크롭: 씬별 trim→crop→스케일→자막 번인→concat."""
    import tempfile as _tmp
    tmpdir = Path(_tmp.mkdtemp(prefix="qc_smart_"))

    parts = []
    for i, s in enumerate(plan):
        seg = tmpdir / f"seg_{i:03d}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(src),
            "-ss", f"{s['start']:.3f}", "-to", f"{s['end']:.3f}",
            "-vf", (
                f"crop={s['src_w']}:{s['src_h']}:{s['src_x']}:0,"
                "scale=1080:1920,fps=30,format=yuv420p"
            ),
            "-c:v", "libx264", "-preset", "medium", "-crf", "17",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(seg),
        ]
        run(cmd)
        parts.append(seg)

    concat_out = tmpdir / "concat.mp4"
    concat_clips(parts, concat_out)

    # 최종 자막 번인 단계
    vf_parts = []
    if segments:
        font = find_korean_font()
        caption_vf = build_caption_filter(
            segments, preset_name, font,
            word_highlight=word_highlight, highlight_color=highlight_color)
        if caption_vf:
            vf_parts.append(caption_vf)

    cmd = ["ffmpeg", "-y", "-i", str(concat_out)]
    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "17",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out),
    ]
    run(cmd)
