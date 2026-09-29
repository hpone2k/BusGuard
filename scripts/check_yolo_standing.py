"""Real model/gating smoke test on a bundled image, isolated from live bus state.

Stop the main server first to avoid duplicate GPU models. This is not a cabin
accuracy evaluation: the sample only shows standing people outside a bus.
"""
import json
from pathlib import Path
import statistics
import sys
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import run
import cv2
from vision.backends import Hybrid
from vision.config import Settings
from vision.detection_gate import DetectionGate
from vision.schema import Options
from vision.sessions import Sessions


def main():
    backend = Hybrid(Settings())
    backend.warmup()
    assert backend.info['seating']['available']
    assert 'vision.locate_runtime' not in sys.modules and 'vision.mediapipe_pose' not in sys.modules
    calls = 0
    original_detect = backend.objects.detect
    def counted_detect(frame, options):
        nonlocal calls
        if options.prompt == 'standing person':
            calls += 1
        return original_detect(frame, options)
    backend.objects.detect = counted_detect
    policy = dict(enabled=True, generation=0, posture_enabled=False, posture_generation=0)
    sessions = Sessions(Settings(), SimpleNamespace(cancel=lambda key: None),
                        detection_gate=DetectionGate(lambda: policy))
    source = sessions.create(Options(prompt='person', posture_enabled=True), 'live', 'inside')
    frame = cv2.imread(str(ROOT / 'static' / 'sample.jpg'))
    canceled = threading.Event()
    began = time.monotonic()
    durations, posture_ms, counts, standing = [], [], [], []
    try:
        for index in range(90):
            start = time.monotonic()
            if index == 15:
                assert calls == 0
                policy.update(posture_enabled=True, posture_generation=1)
            if index == 75:
                policy.update(posture_enabled=False, posture_generation=2)
            before = calls
            source.accept(index)
            result = sessions.infer(source, frame, index, (start - began) * 1000, None, backend, canceled, 0)
            sessions.finalize(source, result, canceled, received=(start, time.time() * 1000))
            summary = result['seating_summary']
            assert summary['seated'] is None and summary['mode'] == 'standing_only'
            if 15 <= index < 75:
                assert calls == before + 1
                assert summary['status'] == 'observed'
                assert set(summary['count_summary']['classes']) == {'standing person'}
                durations.append((time.monotonic() - start) * 1000)
                posture_ms.append(result['seating_ms'])
                counts.append(result['count_summary']['classes']['person']['stable'])
                standing.append(summary['standing'])
            else:
                assert calls == before and summary['status'] == 'paused'
            time.sleep(max(0, .1 - (time.monotonic() - start)))
        assert any(standing), 'The bundled standing sample yielded no standing detections'
        assert all(value == 4 for value in counts), 'Standing labels affected the person census'
        report = dict(engine=backend.info['seating'], frames=90, count_only_frames=30,
                      standing_frames=60, standing_inference_calls=calls,
                      total_median_ms=round(statistics.median(durations), 1),
                      total_max_ms=round(max(durations), 1),
                      standing_pass_median_ms=round(statistics.median(posture_ms), 1),
                      standing_counts=sorted(set(standing)), passenger_counts=sorted(set(counts)),
                      sitting_inferred=False, open_door_inference_calls=0,
                      mediapipe_loaded=False, locateanything_loaded=False)
        (ROOT / 'validation' / 'yolo-standing-integration.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    finally:
        sessions.delete(source.id)
        backend.close()


if __name__ == '__main__':
    main()
