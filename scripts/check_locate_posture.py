"""Local model smoke test; no live cameras or controller journal are changed.

Stop the main server first to avoid loading duplicate GPU models. This script
checks gating and concurrent latency on the bundled street image, not accuracy.
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
import run  # Sets the same local cache directories as the main server.
import cv2
from vision.backends import Hybrid
from vision.config import Settings
from vision.detection_gate import DetectionGate
from vision.schema import Options
from vision.sessions import Sessions


def main():
    backend = Hybrid(Settings())
    backend.warmup()
    assert backend.info['seating']['available'], backend.info['seating']['error']
    policy = dict(enabled=True, generation=0, posture_enabled=False, posture_generation=0)
    sessions = Sessions(Settings(), SimpleNamespace(cancel=lambda key: None),
                        detection_gate=DetectionGate(lambda: policy))
    source = sessions.create(Options(prompt='person', posture_enabled=True, size=640), 'live', 'inside')
    frame = cv2.imread(str(ROOT / 'static' / 'sample.jpg'))
    canceled = threading.Event()
    counters, posture, latencies = [], {}, []
    began = time.monotonic()
    baseline = backend.objects.seating.sequence
    try:
        for index in range(180):
            start = time.monotonic()
            if index == 15:
                assert backend.objects.seating.sequence == baseline, 'Open doors scheduled posture work'
                policy.update(posture_enabled=True, posture_generation=1)
            if index == 155:
                policy.update(posture_enabled=False, posture_generation=2)
            source.accept(index)
            result = sessions.infer(source, frame, index, (start - began) * 1000,
                                    None, backend, canceled, 0)
            sessions.finalize(source, result, canceled, received=(start, time.time() * 1000))
            summary = result['seating_summary']
            if index < 15 or index >= 155:
                assert summary['status'] == 'paused' and not summary.get('standing'), summary
            if 15 <= index < 155:
                latencies.append((time.monotonic() - start) * 1000)
                count = result['count_summary']['classes']['person']
                counters.append(count['stable'] if count['status'] == 'stable' else None)
            if summary.get('posture_frame_id') is not None:
                posture[summary['posture_frame_id']] = {key: summary.get(key) for key in
                    ('status', 'standing', 'seated', 'unknown', 'complete', 'age_ms', 'analysis_ms', 'roster_verified')}
            time.sleep(max(0, .12 - (time.monotonic() - start)))
        report = dict(model=backend.info['seating'], frames=180,
                      generic_median_ms=round(statistics.median(latencies), 1),
                      generic_max_ms=round(max(latencies), 1), stable_person_frames=sum(x is not None for x in counters),
                      posture_results=posture, no_posture_with_open_doors=True)
        assert posture, 'No completed semantic inference was observed'
        assert any(row.get('standing', 0) for row in posture.values()), 'No standing labels were published'
        assert sum(x is not None for x in counters) > len(counters) * .8, 'Fast person count did not stay stable'
        (ROOT / 'validation' / 'locate-integration.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    finally:
        sessions.delete(source.id)
        backend.close()


if __name__ == '__main__':
    main()
