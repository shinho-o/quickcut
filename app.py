"""
QuickCut — 브이로그 빠른 편집기.
여러 영상 업로드 → 자동 자막 + 무음 감지 → 수동 다듬기 → 내보내기.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import traceback
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, jsonify, render_template, request,
                   send_from_directory, url_for)
from werkzeug.utils import secure_filename

import highlight as highlight_mod
import processor
import silence as silence_mod
import smart_crop as smart_crop_mod
import smart_edit as smart_edit_mod


def _gen_preview_thumbs(video: Path, out_dir: Path, job_id: str, n: int = 6):
    """영상에서 n 장의 썸네일 추출 → data/<pid>/output/thumbs_<job>/"""
    import subprocess as _sp
    tdir = out_dir / f"thumbs_{job_id}"
    tdir.mkdir(exist_ok=True)
    # ffprobe 로 duration 얻어서 균등 분할
    r = _sp.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True)
    try:
        dur = float(r.stdout.strip() or 0)
    except ValueError:
        dur = 0
    if dur <= 0:
        return
    for i in range(n):
        t = dur * (i + 0.5) / n
        out = tdir / f"{i:02d}.jpg"
        _sp.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", "scale=320:-1", "-q:v", "5",
             str(out)], capture_output=True)


def _shift_segment(seg: dict, delta: float) -> dict:
    """세그먼트 + 내부 words 의 시간을 delta 만큼 이동한 복사본 반환."""
    out = dict(seg)
    out["start"] = round(seg["start"] + delta, 2)
    out["end"] = round(seg["end"] + delta, 2)
    if seg.get("words"):
        out["words"] = [
            {"start": round(w["start"] + delta, 2),
             "end": round(w["end"] + delta, 2),
             "word": w["word"]}
            for w in seg["words"]
        ]
    return out

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1GB

# 메모리 내 작업 상태
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()

# 프로젝트별 메타 쓰기 락 (레이스 방지)
_META_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
# 프로젝트별 내보내기 중복 방지 락
_EXPORT_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)


# ───────── 유틸 ─────────

def project_dir(pid: str) -> Path:
    d = PROJECTS_DIR / pid
    if not d.exists():
        abort(404)
    return d


def project_meta(pid: str) -> dict:
    """디스크에서 최신 meta 로드. 파일 없으면 404."""
    p = project_dir(pid) / "project.json"
    if not p.exists():
        abort(404)
    return json.loads(p.read_text(encoding="utf-8"))


def save_meta(pid: str, data: dict):
    """임시 파일 쓰고 atomic rename — 중간 크래시에도 파일 손상 방지."""
    target = project_dir(pid) / "project.json"
    with _META_LOCKS[pid]:
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)


def merge_clip_patch(pid: str, clip_id: str, patch: dict):
    """meta 를 다시 로드해 해당 클립만 merge 후 저장 — 레이스 안전."""
    with _META_LOCKS[pid]:
        target = project_dir(pid) / "project.json"
        meta = json.loads(target.read_text(encoding="utf-8"))
        for c in meta.get("clips", []):
            if c["id"] == clip_id:
                c.update({k: v for k, v in patch.items() if k != "id"})
                break
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)


# ───────── 라우트: 메인/프로젝트 생성 ─────────

@app.route("/")
def index():
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta = json.loads((d / "project.json").read_text(encoding="utf-8")) \
            if (d / "project.json").exists() else {}
        projects.append({
            "id": d.name,
            "title": meta.get("title", d.name),
            "created": meta.get("created", ""),
            "clip_count": len(meta.get("clips", [])),
            "status": meta.get("status", "신규"),
        })
    return render_template("index.html", projects=projects)


@app.route("/project/new", methods=["POST"])
def project_new():
    files = request.files.getlist("videos")
    if not files:
        return jsonify({"error": "영상을 하나 이상 올려주세요"}), 400

    pid = uuid.uuid4().hex[:8]
    pdir = PROJECTS_DIR / pid
    pdir.mkdir(parents=True)
    (pdir / "clips").mkdir()

    clips = []
    for i, f in enumerate(files):
        if not f.filename:
            continue
        name = secure_filename(f.filename) or f"clip_{i}.mp4"
        dest = pdir / "clips" / f"{i:02d}_{name}"
        f.save(dest)
        try:
            duration = processor.probe_duration(dest)
            w, h = processor.probe_resolution(dest)
        except Exception:
            duration, w, h = 0.0, 0, 0
        clips.append({
            "id": f"{i:02d}",
            "filename": dest.name,
            "duration": round(duration, 2),
            "width": w,
            "height": h,
            "trim_start": 0.0,
            "trim_end": round(duration, 2),
            "segments": [],
            "silence_ranges": [],
        })

    title = request.form.get("title", "").strip() or f"프로젝트 {datetime.now():%m-%d %H:%M}"
    meta = {
        "id": pid,
        "title": title,
        "created": datetime.now().isoformat(timespec="seconds"),
        "clips": clips,
        "style_preset": "minimal",
        "orientation": "original",   # original | vertical
        "skip_silence": True,
        "smart_crop": True,          # 세로 선택 시 얼굴 추적 크롭
        "auto_highlight": False,     # 자동 하이라이트 선택
        "highlight_duration": 60,    # 목표 길이 (초)
        "smart_edit": False,         # 단어 단위 점프컷 + 간투어 제거
        "remove_fillers": True,
        "aggressive_fillers": False,
        "jump_gap": 0.4,
        "word_highlight": False,     # Opus Clip 스타일 한 단어씩 팝업
        "highlight_color": "#FFD080",
        "status": "준비됨",
    }
    save_meta(pid, meta)
    return jsonify({"id": pid, "redirect": url_for("project_page", pid=pid)})


@app.route("/project/<pid>")
def project_page(pid):
    meta = project_meta(pid)
    return render_template(
        "project.html",
        meta=meta,
        style_presets=processor.get_presets(),
    )


@app.route("/project/<pid>/delete", methods=["POST"])
def project_delete(pid):
    d = project_dir(pid)
    shutil.rmtree(d)
    return jsonify({"ok": True})


# ───────── 클립별 미디어 / 메타 업데이트 ─────────

@app.route("/project/<pid>/clip/<cid>/video")
def clip_video(pid, cid):
    meta = project_meta(pid)
    clip = next((c for c in meta["clips"] if c["id"] == cid), None)
    if not clip:
        abort(404)
    return send_from_directory(project_dir(pid) / "clips", clip["filename"])


@app.route("/project/<pid>/update", methods=["POST"])
def project_update(pid):
    data = request.get_json() or {}

    # 프로젝트 단위 필드
    with _META_LOCKS[pid]:
        target = project_dir(pid) / "project.json"
        meta = json.loads(target.read_text(encoding="utf-8"))

        if "title" in data:
            t = (data["title"] or "").strip()
            if t:
                meta["title"] = t
        if "style_preset" in data:
            meta["style_preset"] = data["style_preset"]
        if "orientation" in data:
            meta["orientation"] = data["orientation"]
        if "skip_silence" in data:
            meta["skip_silence"] = bool(data["skip_silence"])
        if "smart_crop" in data:
            meta["smart_crop"] = bool(data["smart_crop"])
        if "auto_highlight" in data:
            meta["auto_highlight"] = bool(data["auto_highlight"])
        if "highlight_duration" in data:
            try:
                meta["highlight_duration"] = max(10, int(data["highlight_duration"]))
            except (TypeError, ValueError):
                pass
        if "smart_edit" in data:
            meta["smart_edit"] = bool(data["smart_edit"])
        if "remove_fillers" in data:
            meta["remove_fillers"] = bool(data["remove_fillers"])
        if "aggressive_fillers" in data:
            meta["aggressive_fillers"] = bool(data["aggressive_fillers"])
        if "jump_gap" in data:
            try:
                meta["jump_gap"] = max(0.1, float(data["jump_gap"]))
            except (TypeError, ValueError):
                pass
        if "word_highlight" in data:
            meta["word_highlight"] = bool(data["word_highlight"])
        if "highlight_color" in data:
            meta["highlight_color"] = str(data["highlight_color"])[:20]

        if "clips" in data:
            by_id = {c["id"]: c for c in meta["clips"]}
            for patch in data["clips"]:
                cid = patch.get("id")
                if cid not in by_id:
                    continue
                for k in ("trim_start", "trim_end", "segments", "silence_ranges"):
                    if k in patch:
                        by_id[cid][k] = patch[k]

        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    return jsonify({"ok": True})


# ───────── 분석 작업 (Whisper + 무음) ─────────

def _run_analyze(pid: str, job_id: str, keep_existing: bool):
    """클립 단위 분석. keep_existing=True 면 이미 segments 가 있는 클립은 건너뜀.

    사용자가 도중에 자막을 수정해도 손실되지 않도록 **매 클립마다 최신 meta
    를 디스크에서 다시 읽어 해당 클립만 patch** 한다.
    """
    try:
        # 처음 한 번만 클립 목록 스냅샷 얻기 (파일 추가/삭제는 없으니 안전)
        initial = json.loads(
            (project_dir(pid) / "project.json").read_text(encoding="utf-8"))
        clip_ids = [c["id"] for c in initial["clips"]]
        total = len(clip_ids)

        for idx, cid in enumerate(clip_ids):
            # 현재 meta 재로드 → 사용자 편집 반영
            current = json.loads(
                (project_dir(pid) / "project.json").read_text(encoding="utf-8"))
            clip = next((c for c in current["clips"] if c["id"] == cid), None)
            if not clip:
                continue

            if keep_existing and clip.get("segments"):
                _JOBS[job_id]["progress"] = f"건너뜀 {idx+1}/{total}: 이미 분석됨"
                continue

            video_path = project_dir(pid) / "clips" / clip["filename"]
            _JOBS[job_id]["progress"] = f"자막 생성 {idx+1}/{total}: {clip['filename']}"
            # 똑똑한 자동 편집을 위해 word-level 타임스탬프도 함께 확보
            segments = processor.transcribe(video_path, word_timestamps=True)

            _JOBS[job_id]["progress"] = f"무음 감지 {idx+1}/{total}/{total}"
            try:
                sil = silence_mod.detect_silence_ranges(video_path, segments)
            except Exception:
                sil = []

            merge_clip_patch(pid, cid, {
                "segments": segments,
                "silence_ranges": sil,
            })

        # 상태 업데이트 (전체 meta 재로드 후 수정)
        with _META_LOCKS[pid]:
            target = project_dir(pid) / "project.json"
            m = json.loads(target.read_text(encoding="utf-8"))
            m["status"] = "분석 완료"
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, target)

        _JOBS[job_id].update({"status": "done", "progress": "완료"})
    except Exception as e:
        traceback.print_exc()
        _JOBS[job_id].update({"status": "error", "error": str(e)})


@app.route("/project/<pid>/analyze", methods=["POST"])
def project_analyze(pid):
    """?force=1 이면 모든 클립 재분석 (기존 수정 자막 덮어씀).
    기본: 이미 segments 있는 클립은 건너뜀.
    """
    keep_existing = (request.args.get("force") != "1")
    job_id = uuid.uuid4().hex[:8]
    with _JOBS_LOCK:
        _JOBS[job_id] = {"status": "running", "progress": "시작 중"}
    threading.Thread(
        target=_run_analyze, args=(pid, job_id, keep_existing),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/job/<job_id>")
def job_status(job_id):
    j = _JOBS.get(job_id)
    if not j:
        return jsonify({"status": "unknown"}), 404
    return jsonify(j)


# ───────── 내보내기 ─────────

def _run_export(pid: str, job_id: str):
    lock = _EXPORT_LOCKS[pid]
    if not lock.acquire(blocking=False):
        _JOBS[job_id].update({"status": "error",
                              "error": "이미 내보내기가 진행 중입니다."})
        return
    try:
        meta = project_meta(pid)
        pdir = project_dir(pid)
        out_dir = pdir / "output"
        out_dir.mkdir(exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="qc_export_"))

        trimmed_clips: list[Path] = []
        offset = 0.0
        combined_segments: list[dict] = []

        # ── 자동 하이라이트: 모든 클립의 segment 를 점수화해 상위 구간만 남김 ──
        auto_h = meta.get("auto_highlight")
        hi_target = float(meta.get("highlight_duration", 60))
        highlight_filter: dict[str, list[tuple[float, float]]] = {}
        if auto_h:
            _JOBS[job_id]["progress"] = "하이라이트 점수 계산"
            all_scored: list[dict] = []
            for clip in meta["clips"]:
                src_p = pdir / "clips" / clip["filename"]
                scored = highlight_mod.score_segments(src_p, clip.get("segments", []))
                for s in scored:
                    s["_clip_id"] = clip["id"]
                all_scored.extend(scored)
            picked = highlight_mod.pick_top(all_scored, hi_target)
            for s in picked:
                highlight_filter.setdefault(s["_clip_id"], []).append(
                    (s["start"], s["end"]))

        smart_edit_on = meta.get("smart_edit")

        for idx, clip in enumerate(meta["clips"]):
            src = pdir / "clips" / clip["filename"]
            t0 = float(clip.get("trim_start", 0.0))
            t1 = float(clip.get("trim_end", clip.get("duration", 0.0)))

            segs = clip.get("segments", [])

            # ── 똑똑한 자동 편집: 단어 단위 점프컷 + 간투어 제거 ──
            if smart_edit_on and segs:
                _JOBS[job_id]["progress"] = f"똑똑한 편집 {idx+1}번 클립"
                # 트림 범위 안의 세그먼트만 전달
                in_range = [s for s in segs
                            if t0 <= s["start"] and s["end"] <= t1]
                plan = smart_edit_mod.build_keep_plan(
                    in_range,
                    max_gap_sec=float(meta.get("jump_gap", 0.4)),
                    remove_fillers=meta.get("remove_fillers", True),
                    aggressive_fillers=meta.get("aggressive_fillers", False),
                )
                if plan["keep_ranges"]:
                    keep = [(t0 + a, t0 + b) for a, b in plan["keep_ranges"]]
                    shifted = smart_edit_mod.words_to_segments(plan["kept_words"])
                    # 각 keep 범위 재인코딩 트림 후 컨캣
                    for k_idx, (a, b) in enumerate(keep):
                        tr = tmp_dir / f"clip_{idx:02d}_{k_idx:02d}.mp4"
                        _JOBS[job_id]["progress"] = \
                            f"트리밍 {idx+1}번 ({k_idx+1}/{len(keep)})"
                        processor.trim_clip(src, tr, a, b)
                        trimmed_clips.append(tr)
                    for s in shifted:
                        combined_segments.append({
                            "start": round(s["start"] + offset, 2),
                            "end": round(s["end"] + offset, 2),
                            "text": s["text"],
                        })
                    offset += sum(b - a for a, b in keep)
                    continue

            if auto_h:
                ranges = highlight_filter.get(clip["id"], [])
                if not ranges:
                    continue  # 선택된 하이라이트 없으면 이 클립 건너뜀
                keep: list[tuple[float, float]] = sorted(ranges)
                # 하이라이트 범위에 속한 세그먼트만 포함 + 각 범위 0 기준으로 시프트
                shifted = []
                running = 0.0
                for a, b in keep:
                    for s in segs:
                        if s["start"] >= a and s["end"] <= b:
                            shifted.append({
                                "start": round(s["start"] - a + running, 2),
                                "end": round(s["end"] - a + running, 2),
                                "text": s["text"],
                            })
                    running += b - a
            else:
                keep = [(t0, t1)]
                shifted = [_shift_segment(s, -t0)
                           for s in segs if t0 <= s["start"] and s["end"] <= t1]

                if meta.get("skip_silence") and clip.get("silence_ranges"):
                    in_range_silence = [
                        r for r in clip["silence_ranges"]
                        if r.get("suggest_skip") and r["start"] >= t0 and r["end"] <= t1
                    ]
                    rebased = list(shifted)  # 이미 t0 기준으로 이동된 복사본
                    rebased_silence = [{**r, "start": r["start"] - t0,
                                        "end": r["end"] - t0}
                                       for r in in_range_silence]
                    keep_rel, shifted = silence_mod.apply_skips(
                        clip_duration=t1 - t0,
                        segments=rebased,
                        skip_ranges=rebased_silence,
                    )
                    keep = [(t0 + a, t0 + b) for a, b in keep_rel]

            # 각 keep 범위를 재인코딩 트림
            for k_idx, (a, b) in enumerate(keep):
                trimmed = tmp_dir / f"clip_{idx:02d}_{k_idx:02d}.mp4"
                _JOBS[job_id]["progress"] = \
                    f"트리밍 {idx+1}번 클립 ({k_idx+1}/{len(keep)})"
                processor.trim_clip(src, trimmed, a, b)
                trimmed_clips.append(trimmed)

            # 세그먼트를 최종 타임라인에 맞춰 이동
            for s in shifted:
                combined_segments.append(_shift_segment(s, offset))
            offset += sum(b - a for a, b in keep)

        _JOBS[job_id]["progress"] = "이어붙이는 중"
        concat_out = tmp_dir / "concat.mp4"
        processor.concat_clips(trimmed_clips, concat_out)

        _JOBS[job_id]["progress"] = "자막·포맷 적용"
        _JOBS[job_id]["phase"] = "captions"
        final_out = out_dir / f"result_{job_id}.mp4"

        crop_plan = None
        if meta.get("orientation") == "vertical" and meta.get("smart_crop"):
            _JOBS[job_id]["progress"] = "얼굴 추적 분석 중"
            _JOBS[job_id]["phase"] = "face_detect"
            try:
                crop_plan = smart_crop_mod.plan_smart_crop(concat_out)
            except Exception:
                traceback.print_exc()
                crop_plan = None

        # 썸네일 생성 — 진행 시각화용 (concat 중간 프레임 몇 장)
        try:
            _gen_preview_thumbs(concat_out, out_dir, job_id)
            _JOBS[job_id]["thumbs_url"] = f"/project/{pid}/thumbs/{job_id}"
        except Exception:
            pass

        processor.apply_effects(
            concat_out, final_out,
            segments=combined_segments,
            preset_name=meta.get("style_preset", "minimal"),
            vertical=(meta.get("orientation") == "vertical"),
            smart_crop_plan=crop_plan,
            word_highlight=bool(meta.get("word_highlight")),
            highlight_color=meta.get("highlight_color", "#FFD080"),
        )

        # 최종 타임라인 세그먼트도 저장 (디스크 최신 meta 에 merge)
        with _META_LOCKS[pid]:
            target = pdir / "project.json"
            m = json.loads(target.read_text(encoding="utf-8"))
            m["last_export"] = {
                "filename": final_out.name,
                "created": datetime.now().isoformat(timespec="seconds"),
                "duration": round(offset, 2),
                "segments": combined_segments,
            }
            m["status"] = "내보내기 완료"
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, target)

        _JOBS[job_id].update({
            "status": "done",
            "progress": "완료",
            "download_url": f"/project/{pid}/download/{final_out.name}",
        })
    except Exception as e:
        traceback.print_exc()
        _JOBS[job_id].update({"status": "error", "error": str(e)})
    finally:
        lock.release()


@app.route("/project/<pid>/export", methods=["POST"])
def project_export(pid):
    job_id = uuid.uuid4().hex[:8]
    _JOBS[job_id] = {"status": "running", "progress": "준비 중"}
    threading.Thread(target=_run_export, args=(pid, job_id), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/project/<pid>/download/<fname>")
def project_download(pid, fname):
    out_dir = project_dir(pid) / "output"
    return send_from_directory(out_dir, fname, as_attachment=True)


@app.route("/project/<pid>/thumbs/<job_id>/<int:idx>")
def project_thumbs(pid, job_id, idx):
    tdir = project_dir(pid) / "output" / f"thumbs_{job_id}"
    return send_from_directory(tdir, f"{idx:02d}.jpg")


# ───────── 실행 ─────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7400))
    print(f"\n  QuickCut  →  http://localhost:{port}\n")
    from waitress import serve
    serve(app, host="0.0.0.0", port=port, threads=4)
