"""Exercise actual live-session HTTP inference and generate a reproducible video fixture."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import run
import cv2
import httpx
import numpy as np
from vision.config import Settings
from vision.media import ffmpeg_path, subprocess_flags
from vision.schema import Detection
from vision.tracking import StableTracker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:4479')
    args = parser.parse_args()
    directory = ROOT / 'validation'
    directory.mkdir(exist_ok=True)
    sample = cv2.resize(cv2.imread(str(ROOT / 'static/sample.jpg')), (480, 640))
    frames = []
    for i in range(80):
        if 40 <= i < 42 or 55 <= i < 65:
            frame = np.zeros_like(sample)
        else:
            dx = 0 if i < 20 else 12 * np.sin((i - 20) / 15)
            frame = cv2.warpAffine(sample, np.float32([[1, 0, dx], [0, 1, 0]]), (480, 640))
        frames.append(frame)
    video_path = directory / 'stability-demo.mp4'
    command = [ffmpeg_path(Settings()), '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'rawvideo', '-pixel_format', 'bgr24', '-video_size', '480x640', '-framerate', '30',
        '-i', 'pipe:0', '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(video_path)]
    result = subprocess.run(command, input=b''.join(frame.tobytes() for frame in frames),
                            capture_output=True, timeout=30, **subprocess_flags())
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    timings, results = [], []
    with httpx.Client(base_url=args.url, timeout=120) as client:
        status = client.get('/api/status').json()
        assert status['state'] == 'ready', status
        response = client.post('/api/sessions', json={'kind':'live', 'options':{
            'prompt':'person, bus', 'size':640, 'confidence':.35, 'stabilization':'balanced', 'camera_motion':True}})
        response.raise_for_status()
        session = response.json()['id']
        try:
            for i, frame in enumerate(frames):
                start = time.perf_counter()
                ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                assert ok
                response = client.post(f'/api/sessions/{session}/frames', params={'frame_id':i, 'captured_at':i*1000/30}, content=encoded.tobytes())
                response.raise_for_status()
                result = response.json()
                timings.append({'total_ms':(time.perf_counter()-start)*1000,
                                'inference_ms':result['inference_ms'], 'tracking_ms':result['tracking_ms']})
                results.append(result)
            assert not results[0]['detections'], 'One-frame candidates should not be shown.'
            assert results[1]['detections'], 'The sample should produce confirmed detections.'
            assert any(d['predicted'] for d in results[40]['detections']), 'A short miss should be bridged.'
            assert not results[61]['detections'], 'A long miss must clear the overlay.'
        finally:
            client.delete(f'/api/sessions/{session}').raise_for_status()
    # Controlled jitter experiment has known stationary truth, independent of model accuracy.
    rng, tracker = np.random.default_rng(4479), StableTracker()
    raw, stable = [], []
    for i in range(100):
        x = .2 + rng.normal(0, .004)
        dets = tracker.update([Detection('object', [x,.2,x+.15,.5], .8)], i/30)
        if i > 10:
            raw.append(x); stable.append(dets[0].bbox[0])
    warm = timings[5:]
    report = {'backend':status['backend'], 'input':'480x640 sample with camera translations and blank intervals',
        'source_frames':80, 'source_fps':30, 'detector_size':640,
        'note':'Local JPEG encoding + HTTP; excludes physical camera capture and browser display. Source timestamps simulate 30 FPS.',
        'warm_total_median_ms':round(float(np.median([x['total_ms'] for x in warm])),2),
        'warm_total_p95_ms':round(float(np.percentile([x['total_ms'] for x in warm],95)),2),
        'warm_model_median_ms':round(float(np.median([x['inference_ms'] for x in warm])),2),
        'warm_tracking_median_ms':round(float(np.median([x['tracking_ms'] for x in warm])),2),
        'first_request_ms':round(timings[0]['total_ms'],2),
        'confirmation_passed':True, 'short_miss_recovery_passed':True, 'long_miss_expiry_passed':True,
        'synthetic_stationary_jitter_std_reduction_percent':round((1-np.std(stable)/np.std(raw))*100,1)}
    (ROOT/'benchmark-stability.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (directory/'live-responses.json').write_text(json.dumps(results),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
