"""
얼굴 추적 스마트 크롭.

영상에서 얼굴을 찾아 구간별로 "피사체를 중심에 두는" 크롭 좌표를 계산한다.
크롭은 `scene_crops` 리스트로 반환되어 processor 가 ffmpeg 필터로 적용.
"""
from __future__ import annotations

from pathlib import Path


def _cascade_path() -> str:
    import cv2
    return cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


def plan_smart_crop(
    video: Path,
    target_ratio: tuple[int, int] = (9, 16),
    sample_fps: float = 2.0,
    smooth_window: float = 2.0,
    min_scene_sec: float = 1.0,
) -> list[dict]:
    """
    반환:
        [{"start": float, "end": float, "src_x": int, "src_w": int,
          "src_h": int, "in_w": int, "in_h": int}, ...]

    src_x: 원본에서 잘라낼 좌상단 X (얼굴 중심 기준)
    src_w / src_h: 잘라낼 너비/높이 (target_ratio 유지)
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    in_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    in_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = total / fps if fps else 0

    # 출력 비율에 맞는 crop 창 크기 (원본 높이 기준)
    tw, th = target_ratio
    src_h = in_h
    src_w = int(round(in_h * tw / th))
    if src_w > in_w:
        # 원본이 이미 세로형이면 반대로 계산
        src_w = in_w
        src_h = int(round(in_w * th / tw))

    cascade = cv2.CascadeClassifier(_cascade_path())
    step = int(round(fps / sample_fps)) if sample_fps else 1
    samples: list[tuple[float, float | None]] = []

    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(
                gray, scaleFactor=1.2, minNeighbors=4, minSize=(40, 40))
            t = idx / fps
            if len(faces) > 0:
                # 가장 큰 얼굴 중심 x
                faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
                x, y, w, h = faces[0]
                samples.append((t, x + w / 2.0))
            else:
                samples.append((t, None))
        idx += 1
    cap.release()

    if not samples:
        return [{"start": 0, "end": duration, "src_x": (in_w - src_w) // 2,
                 "src_w": src_w, "src_h": src_h, "in_w": in_w, "in_h": in_h}]

    # 결측 보간 (앞·뒤 값)
    xs = [s[1] for s in samples]
    last = None
    for i, v in enumerate(xs):
        if v is not None:
            last = v
            break
    if last is None:
        last = in_w / 2.0
    for i, v in enumerate(xs):
        if v is None:
            xs[i] = last
        else:
            last = v

    # 이동 평균 스무딩
    win = max(1, int(smooth_window * sample_fps))
    arr = np.array(xs, dtype=float)
    kernel = np.ones(win) / win
    smoothed = np.convolve(arr, kernel, mode="same")

    # 씬 변화(급격한 x 차이) 기반 구간 분할
    times = [s[0] for s in samples]
    scenes: list[dict] = []
    cur_start = 0.0
    cur_xs = [smoothed[0]]
    for i in range(1, len(samples)):
        delta = abs(smoothed[i] - smoothed[i - 1])
        t = times[i]
        # 0.25 * src_w 이상 급변 + 최소 길이 확보 → 새 씬
        if delta > 0.25 * src_w and (t - cur_start) >= min_scene_sec:
            scenes.append({
                "start": round(cur_start, 2),
                "end": round(t, 2),
                "face_x": float(np.mean(cur_xs)),
            })
            cur_start = t
            cur_xs = [smoothed[i]]
        else:
            cur_xs.append(smoothed[i])
    scenes.append({
        "start": round(cur_start, 2),
        "end": round(duration, 2),
        "face_x": float(np.mean(cur_xs)),
    })

    out = []
    for s in scenes:
        x = int(round(s["face_x"] - src_w / 2))
        x = max(0, min(in_w - src_w, x))
        out.append({
            "start": s["start"], "end": s["end"],
            "src_x": x, "src_w": src_w, "src_h": src_h,
            "in_w": in_w, "in_h": in_h,
        })
    return out
