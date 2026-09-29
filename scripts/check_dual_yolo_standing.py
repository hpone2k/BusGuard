"""Exercise real YOLOE on two roles without touching the live bus or cameras.

Run while the server is stopped, to avoid loading a second GPU model. The
bundled street image is repeated for latency and pipeline checks; it is not
an accuracy evaluation on real cabin footage.
"""
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import run  # Configures local model caches before importing GPU libraries.
import cv2

from vision.backends import Hybrid
from vision.config import Settings
from vision.detection_gate import DetectionGate, DetectionPausedError
from vision.scheduler import Dispatcher
from vision.schema import Options
from vision.sessions import Sessions


def main():
    policy = dict(enabled=True, generation=0, inside_enabled=True,
                  inside_generation=0, posture_enabled=False, posture_generation=0)
    dispatcher = Dispatcher(lambda: Hybrid(Settings()))
    dispatcher.start()
    sessions = Sessions(Settings(), dispatcher, detection_gate=DetectionGate(lambda: policy.copy()))
    image = cv2.imread(str(ROOT / 'static' / 'sample.jpg'))
    if image is None:
        raise RuntimeError('The bundled local sample could not be read.')
    sources = {
        'inside': sessions.create(Options(prompt='person', posture_enabled=True), 'live', 'inside'),
        'outside': sessions.create(Options(prompt='person, wheelchair, stroller, walker, crutch, cane'),
                                   'live', 'outside'),
    }
    frame_ids = dict.fromkeys(sources, 0)
    all_rows, pass_counts, observed_prompts = [], {}, []
    try:
        limit = time.monotonic() + 180
        while dispatcher.status()['state'] != 'ready':
            status = dispatcher.status()
            if status['state'] == 'error' or time.monotonic() >= limit:
                raise RuntimeError(status.get('error') or 'Model startup timed out.')
            time.sleep(.05)
        backend = dispatcher.backend
        assert backend.info['seating']['available']
        original_detect = backend.objects.detect

        def observed_detect(frame, options):
            prompt = tuple(options.categories())
            observed_prompts.append(prompt)
            pass_counts[prompt] = pass_counts.get(prompt, 0) + 1
            return original_detect(frame, options)

        backend.objects.detect = observed_detect
        for phase, rounds in (('boarding', 12), ('closed_doors', 20), ('travelling', 12)):
            policy.update(posture_enabled=phase != 'boarding',
                          posture_generation=0 if phase == 'boarding' else 1,
                          enabled=phase != 'travelling', generation=1 if phase == 'travelling' else 0)
            for step in range(rounds):
                active_roles = ['inside'] if phase == 'travelling' else (
                    ['inside', 'outside'] if step % 2 == 0 else ['outside', 'inside'])
                if phase == 'travelling':
                    try:
                        sessions.detection_gate.check(source_role='outside')
                    except DetectionPausedError:
                        pass
                    else:
                        raise AssertionError('Outside inference was admitted during travel.')
                pending = []
                for role in active_roles:
                    source = sources[role]
                    frame_ids[role] += 1
                    frame_id = frame_ids[role]
                    received = (time.monotonic(), time.time() * 1000)
                    captured_at = time.perf_counter() * 1000
                    admission = sessions.detection_gate.capture(role)
                    source.accept(frame_id)
                    work = dispatcher.submit('session:' + source.id,
                        lambda model, canceled, queued, source=source, frame_id=frame_id,
                               captured_at=captured_at, admission=admission: sessions.infer(
                            source, image, frame_id, captured_at, None, model, canceled, queued,
                            admission['generation'], admission['posture_token']))
                    pending.append((role, source, work, received))
                for role, source, work, received in pending:
                    result = work.future.result(timeout=30)
                    sessions.finalize(source, result, work.canceled, received=received)
                    summary = result.get('seating_summary')
                    if role == 'outside':
                        assert summary is None
                        assert all(detection['label'] != 'standing person' for detection in result['detections'])
                    else:
                        assert summary['seated'] is None
                        assert summary['mode'] == 'standing_only'
                        assert summary['status'] == ('paused' if phase == 'boarding' else 'observed')
                    classes = result['count_summary']['classes']
                    assert 'standing person' not in classes, 'Standing labels leaked into the passenger census.'
                    all_rows.append(dict(
                        phase=phase, role=role, frame_id=result['frame_id'],
                        queue_ms=result['queue_ms'], inference_ms=result['inference_ms'],
                        standing_ms=result['seating_ms'] if role == 'inside' else None,
                        server_ms=round((time.monotonic() - received[0]) * 1000, 1),
                        person_count=classes['person']['stable'],
                        standing=summary['standing'] if summary else None,
                        posture_status=summary['status'] if summary else None))
        assert pass_counts.get(('standing person',)) == 32
        assert pass_counts[('person',)] == 44
        assert pass_counts[('person', 'wheelchair', 'stroller', 'walker', 'crutch', 'cane')] == 32
        grouped = {}
        for phase in ('boarding', 'closed_doors', 'travelling'):
            grouped[phase] = {}
            for role in ('inside', 'outside'):
                rows = [row for row in all_rows if row['phase'] == phase and row['role'] == role]
                if not rows:
                    continue
                grouped[phase][role] = dict(
                    frames=len(rows),
                    median_server_ms=round(statistics.median(row['server_ms'] for row in rows), 1),
                    max_server_ms=max(row['server_ms'] for row in rows),
                    median_queue_ms=round(statistics.median(row['queue_ms'] for row in rows), 1),
                    max_queue_ms=max(row['queue_ms'] for row in rows),
                    median_object_inference_ms=round(statistics.median(row['inference_ms'] for row in rows), 1),
                    stable_person_counts=sorted(set(row['person_count'] for row in rows if row['person_count'] is not None)),
                    standing_counts=sorted(set(row['standing'] for row in rows if row['standing'] is not None)))
        report = dict(
            note='Real model inference on repeated bundled street image. Not real CCTV latency or cabin accuracy.',
            model=backend.info['object_model'], device=backend.info['device'],
            standing_threshold=sources['inside'].options.standing_confidence,
            frames=len(all_rows), phases=grouped,
            standing_inference_calls=pass_counts[('standing person',)],
            open_door_standing_inference_calls=0, outside_travel_inference_calls=0,
            sitting_classification=False,
            max_pending_per_role=1, rows=all_rows)
        path = ROOT / 'validation' / 'dual-yolo-standing.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({key: value for key, value in report.items() if key != 'rows'}, indent=2))
        print(f'Report: {path}')
    finally:
        for source in sources.values():
            sessions.delete(source.id)
        dispatcher.close()


if __name__ == '__main__':
    main()
