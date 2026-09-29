"""Exercise the running hybrid service with real sample frames, without cameras.

Creates temporary test sessions and always closes them. Timings are local HTTP
round trips, not physical-camera latency. All outputs retain test provenance.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:4479'
PHRASES = ['a person wearing a beige coat', 'a person wearing a black coat', 'a blue bus']


def request(path, data=None, method=None):
    headers = {}
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        headers['Content-Type'] = 'application/json'
    elif data is not None:
        headers['Content-Type'] = 'image/jpeg'
    with urlopen(Request(BASE + path, data=data, headers=headers, method=method), timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else None


def main():
    status = request('/api/status')
    if status.get('state') != 'ready':
        raise RuntimeError('Start the hybrid service and wait for model readiness first.')
    sample = (ROOT / 'static/sample.jpg').read_bytes()
    created = []
    report = {'purpose': 'Local HTTP sample diagnostic; not a labeled accuracy dataset or CCTV latency measurement.',
              'phrases': PHRASES, 'size': 512, 'checks': {}, 'timings': {}}

    def create(kind='image', **overrides):
        options = {'mode': 'phrase', 'prompt': '\n'.join(PHRASES), 'size': 512,
                   'confidence': .3, 'stabilization': 'off', 'camera_motion': False, **overrides}
        result = request('/api/sessions', {'kind': kind, 'source_role': 'unassigned', 'options': options})
        session_id = result.get('session_id', result.get('id'))
        if not session_id:
            raise RuntimeError('Session response has no identifier')
        created.append(session_id)
        return session_id

    def frame(session_id, number):
        started = time.perf_counter()
        result = request(f'/api/sessions/{session_id}/frames?frame_id={number}&captured_at={time.time()*1000}', sample)
        return result, (time.perf_counter() - started) * 1000

    try:
        session = create()
        result, elapsed = frame(session, 0)
        assert result['detection_engine'] == 'Grounding DINO'
        assert all(d['label'] in PHRASES for d in result['detections'])
        assert all(0 <= value <= 1 for d in result['detections'] for value in d['bbox'])
        report['checks']['sample'] = {'detections': result['detections'], 'round_trip_ms': round(elapsed, 1)}
        strict = create(class_confidences={label: .95 for label in PHRASES})
        strict_result, _ = frame(strict, 0)
        assert not strict_result['detections'], 'Unexpected very-high-score result on this diagnostic sample'
        report['checks']['per_phrase_thresholds'] = 'all .95 thresholds suppress this sample'
        try:
            create(prompt='\n'.join(f'object number {n}' for n in range(9)))
            raise AssertionError('Nine phrases should fail validation')
        except HTTPError as error:
            assert error.code == 422
        report['checks']['phrase_limit'] = 'nine phrases rejected with HTTP 422'
        # Live is an API transport mode here; unassigned test sessions never feed
        # bus decisions. Avoid the still-image cache in throughput measurements.
        live = create('live')
        times = []
        for number in range(6):
            observation, latency = frame(live, number)
            if number:
                times.append(latency)
        report['timings']['one_source_median_round_trip_ms'] = round(statistics.median(times), 1)
        second = create('live')
        parallel, queue = [], []
        with ThreadPoolExecutor(max_workers=2) as pool:
            for number in range(6, 12):
                calls = [pool.submit(frame, identifier, number) for identifier in (live, second)]
                for call in calls:
                    observation, latency = call.result()
                    parallel.append(latency)
                    queue.append(observation['queue_ms'])
        report['timings']['two_sources_median_round_trip_ms'] = round(statistics.median(parallel), 1)
        report['timings']['two_sources_max_round_trip_ms'] = round(max(parallel), 1)
        report['timings']['two_sources_max_queue_ms'] = round(max(queue), 1)
        output = ROOT / 'validation/phrase-api.json'
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'checks': list(report['checks']), 'timings': report['timings'], 'report': str(output)}, indent=2))
    finally:
        for identifier in created:
            request(f'/api/sessions/{identifier}', method='DELETE')


if __name__ == '__main__':
    main()
