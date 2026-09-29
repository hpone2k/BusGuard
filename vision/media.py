import io
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


def decode_image(data, max_pixels):
    try:
        with Image.open(io.BytesIO(data)) as image:
            w, h = image.size
            if w < 2 or h < 2 or w * h > max_pixels:
                raise ValueError(f"Image must contain 2–{max_pixels:,} pixels and have both dimensions at least 2.")
            image.load()
            return np.asarray(ImageOps.exif_transpose(image).convert("RGB"))[:, :, ::-1].copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid or unsupported image.") from exc


def probe_video(path, max_duration):
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("Video cannot be decoded. Try an MP4 with H.264 video.")
        fps = cap.get(cv2.CAP_PROP_FPS)
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        w, h = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if not all(math.isfinite(x) for x in [fps, count, w, h]) or not 1 <= fps <= 120:
            raise ValueError("Video frame rate must be between 1 and 120 FPS.")
        if count < 1 or w < 2 or h < 2 or w * h > 3840 * 2160:
            raise ValueError("Video must have valid dimensions up to 4K and a known frame count.")
        if count / fps > max_duration:
            raise ValueError(f"Video exceeds the {max_duration / 60:g}-minute limit.")
        ok, frame = cap.read()
        if not ok or frame is None:
            raise ValueError("Video has no readable frames.")
        return {"fps": fps, "frames": int(count), "width": int(w), "height": int(h), "duration": count / fps}
    finally:
        cap.release()


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)


def ffmpeg_path(settings):
    if settings.ffmpeg:
        return settings.ffmpeg
    path = shutil.which("ffmpeg")
    if path:
        return path
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def subprocess_flags():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


PALETTE = [(112, 232, 113), (242, 198, 104), (132, 148, 255), (247, 163, 199), (82, 221, 247)]


def draw_detections(frame, detections):
    h, w = frame.shape[:2]
    for detection in detections:
        box = detection["bbox"]
        x1, y1, x2, y2 = [int(v * scale) for v, scale in zip(box, [w, h, w, h])]
        color = PALETTE[sum(detection["label"].encode()) % len(PALETTE)]
        label = detection["label"]
        if detection.get("track_id") is not None:
            label += f" #{detection['track_id']}"
        if detection.get('predicted'):
            for x in range(x1, x2, 12):
                cv2.line(frame, (x, y1), (min(x+6, x2), y1), color, 2)
                cv2.line(frame, (x, y2), (min(x+6, x2), y2), color, 2)
            for y in range(y1, y2, 12):
                cv2.line(frame, (x1, y), (x1, min(y+6, y2)), color, 2)
                cv2.line(frame, (x2, y), (x2, min(y+6, y2)), color, 2)
        else:
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, label, (x1, max(18, y1 - 7)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return frame


def posture_boxes(summary):
    """Select bounded same-frame semantic boxes, never synthesize from joints."""
    if not isinstance(summary, dict) or summary.get('status') != 'observed':
        return []
    age = summary.get('age_ms', 0)
    ttl = 4000 if summary.get('engine') == 'LocateAnything' else 1000
    if (not isinstance(age, (int, float)) or isinstance(age, bool)
            or not math.isfinite(age) or not 0 <= age < ttl):
        return []
    rows = summary.get('occupants')
    if not isinstance(rows, (list, tuple)):
        return []
    result = []
    for row in rows[:300]:
        if not isinstance(row, dict) or row.get('posture') != 'standing' or row.get('predicted'):
            continue
        box = row.get('bbox')
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                       or not math.isfinite(value) or not 0 <= value <= 1 for value in box)
                or box[2] <= box[0] or box[3] <= box[1]):
            continue
        result.append(row)
    return result


def draw_posture(frame, summary):
    """Label standing detections on their exact analysed preview frame."""
    height, width = frame.shape[:2]
    color, label = (119, 191, 255), 'Standing person'
    outline = (22, 35, 27)
    thickness = max(2, min(4, round(min(width, height) / 300)))
    for occupant in posture_boxes(summary):
        box = occupant['bbox']
        x0, y0, x1, y1 = [round(value * (width - 1 if index % 2 == 0 else height - 1))
                          for index, value in enumerate(box)]
        cv2.rectangle(frame, (x0, y0), (x1, y1), outline, thickness + 3, cv2.LINE_AA)
        cv2.rectangle(frame, (x0, y0), (x1, y1), color, thickness, cv2.LINE_AA)
        scale = max(.4, min(.65, width / 1800))
        (text_width, text_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        x = max(0, min(x0, width - text_width - 12))
        y = min(height - baseline - 5, max(text_height + 8, y0 + text_height + 8))
        cv2.rectangle(frame, (x, max(0, y - text_height - 6)),
                      (min(width - 1, x + text_width + 10), min(height - 1, y + baseline + 4)), outline, -1)
        cv2.putText(frame, label, (x + 5, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)
    return frame
