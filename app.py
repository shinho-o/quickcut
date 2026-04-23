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
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, jsonify, render_template, request,
                   send_from_directory, url_for)
from werkzeug.utils import secure_filename

import processor
import silence as silence_mod

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1GB

# 메모리 내 작업 상태 (지속성 필요 없음)
_JOBS: dict[str, dict] = {}


# ───────── 유틸 ─────────

def project_dir(pid: str) -> Path:
    d = PROJECTS_DIR / pid
    if not d.exists():
        abort(404)
    return d


def project_meta(pid: str) -> dict:
    p = project_dir(pid) / "project.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_meta(pid: str, data: dict):
    (project_dir(pid) / "project.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
        style_presets=processor.STYLE_PRESETS,
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
    meta = project_meta(pid)
    data = request.get_json() or {}

    if "style_preset" in data:
        meta["style_preset"] = data["style_preset"]
    if "orientation" in data:
        meta["orientation"] = data["orientation"]
    if "skip_silence" in data:
        meta["skip_silence"] = bool(data["skip_silence"])
    if "clips" in data:
        # 클립별 트림·세그먼트·스킵 결정 업데이트
        by_id = {c["id"]: c for c in meta["clips"]}
        for patch in data["clips"]:
            cid = patch.get("id")
            if cid not in by_id:
                continue
            for k in ("trim_start", "trim_end", "segments", "silence_ranges"):
                if k in patch:
                    by_id[cid][k] = patch[k]

    save_meta(pid, meta)
    return jsonify({"ok": True})


# ───────── 분석 작업 (Whisper + 무음) ─────────

def _run_analyze(pid: str, job_id: str):
    try:
        meta = project_meta(pid)
        total = len(meta["clips"])
        for idx, clip in enumerate(meta["clips"]):
            _JOBS[job_id]["progress"] = f"전사 {idx+1}/{total}: {clip['filename']}"
            video_path = project_dir(pid) / "clips" / clip["filename"]
            segments = processor.transcribe(video_path)
            clip["segments"] = segments

            _JOBS[job_id]["progress"] = f"무음감지 {idx+1}/{total}"
            try:
                clip["silence_ranges"] = silence_mod.detect_silence_ranges(
                    video_path, segments)
            except Exception:
                clip["silence_ranges"] = []

            save_meta(pid, meta)  # 각 클립 끝날 때마다 저장

        meta["status"] = "분석 완료"
        save_meta(pid, meta)
        _JOBS[job_id].update({"status": "done", "progress": "완료"})
    except Exception as e:
        traceback.print_exc()
        _JOBS[job_id].update({"status": "error", "error": str(e)})


@app.route("/project/<pid>/analyze", methods=["POST"])
def project_analyze(pid):
    job_id = uuid.uuid4().hex[:8]
    _JOBS[job_id] = {"status": "running", "progress": "시작 중"}
    threading.Thread(target=_run_analyze, args=(pid, job_id), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/job/<job_id>")
def job_status(job_id):
    j = _JOBS.get(job_id)
    if not j:
        return jsonify({"status": "unknown"}), 404
    return jsonify(j)


# ───────── 내보내기 ─────────

def _run_export(pid: str, job_id: str):
    try:
        meta = project_meta(pid)
        pdir = project_dir(pid)
        out_dir = pdir / "output"
        out_dir.mkdir(exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="qc_export_"))

        trimmed_clips: list[Path] = []
        offset = 0.0
        combined_segments: list[dict] = []

        for idx, clip in enumerate(meta["clips"]):
            src = pdir / "clips" / clip["filename"]
            t0 = float(clip.get("trim_start", 0.0))
            t1 = float(clip.get("trim_end", clip.get("duration", 0.0)))

            # 스킵 반영
            segs = clip.get("segments", [])
            keep: list[tuple[float, float]] = [(t0, t1)]
            shifted = [dict(s, start=s["start"] - t0, end=s["end"] - t0)
                       for s in segs if t0 <= s["start"] and s["end"] <= t1]

            if meta.get("skip_silence") and clip.get("silence_ranges"):
                # 트림 범위 안의 무음만 고려
                in_range_silence = [
                    r for r in clip["silence_ranges"]
                    if r.get("suggest_skip") and r["start"] >= t0 and r["end"] <= t1
                ]
                # 세그먼트를 트림 0 기준으로 재배치한 뒤 스킵 적용
                rebased = [dict(s, start=s["start"] - t0, end=s["end"] - t0)
                           for s in segs if t0 <= s["start"] and s["end"] <= t1]
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
                combined_segments.append({
                    "start": round(s["start"] + offset, 2),
                    "end": round(s["end"] + offset, 2),
                    "text": s["text"],
                })
            offset += sum(b - a for a, b in keep)

        _JOBS[job_id]["progress"] = "이어붙이는 중"
        concat_out = tmp_dir / "concat.mp4"
        processor.concat_clips(trimmed_clips, concat_out)

        _JOBS[job_id]["progress"] = "자막·포맷 적용"
        final_out = out_dir / f"result_{job_id}.mp4"
        processor.apply_effects(
            concat_out, final_out,
            segments=combined_segments,
            preset_name=meta.get("style_preset", "minimal"),
            vertical=(meta.get("orientation") == "vertical"),
        )

        # 최종 타임라인 세그먼트도 저장
        meta["last_export"] = {
            "filename": final_out.name,
            "created": datetime.now().isoformat(timespec="seconds"),
            "duration": round(offset, 2),
            "segments": combined_segments,
        }
        meta["status"] = "내보내기 완료"
        save_meta(pid, meta)

        _JOBS[job_id].update({
            "status": "done",
            "progress": "완료",
            "download_url": url_for("project_download", pid=pid, fname=final_out.name),
        })
    except Exception as e:
        traceback.print_exc()
        _JOBS[job_id].update({"status": "error", "error": str(e)})


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


# ───────── 실행 ─────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7400))
    print(f"\n  QuickCut  →  http://localhost:{port}\n")
    from waitress import serve
    serve(app, host="0.0.0.0", port=port, threads=4)
