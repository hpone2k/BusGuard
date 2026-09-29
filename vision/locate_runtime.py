"""A latest-frame-only LocateAnything worker, independent of the fast counter.

The cached Linux environment owns the quantized model. Local JPEGs and model
answers cross a private subprocess pipe; no camera images leave this computer.
Cached evidence retains its original capture identity and ages monotonically.
"""
import base64
import copy
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from collections import OrderedDict

from .config import ROOT
from .semantic_seating import estimate as seating_evidence

log = logging.getLogger(__name__)
REVISION = 'c32291ca5e996f5a7a485845b4f57a233936bba0'


def linux_path(path):
    value = Path(path).resolve()
    if os.name == 'nt':
        return '/mnt/' + value.drive[0].lower() + value.as_posix()[2:]
    return str(value)


class LocatePosture:
    def __init__(self, settings, clock=time.monotonic):
        self.settings, self.clock = settings, clock
        self.process = self.log_file = None
        self.messages = queue.Queue(maxsize=8)
        self.condition = threading.Condition()
        self.closed = False
        self.pending = self.active = None
        self.results = OrderedDict()
        self.sequence = 0
        self.worker = None
        self.info = dict(available=False, engine='LocateAnything', model='nvidia/LocateAnything-3B',
                         revision=REVISION, method='LocateAnything semantic posture',
                         asynchronous=True, scores=False, freshness_ms=4000,
                         error='LocateAnything is loading.')

    def _command(self):
        worker = linux_path(ROOT / 'scripts' / 'locate_worker.py')
        command = [self.settings.locate_python, worker]
        if os.name == 'nt':
            command = ['wsl.exe', '-d', self.settings.locate_wsl_distro, '--', *command]
        return command

    def warmup(self, sample):
        try:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            self.log_file = open(self.settings.data_dir / 'locate-runtime.stderr.log', 'w', encoding='utf-8')
            self.process = subprocess.Popen(self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self.log_file, text=True, encoding='utf-8', bufsize=1,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            threading.Thread(target=self._read, name='locate-protocol', daemon=True).start()
            ready = self.messages.get(timeout=90)
            if not ready.get('ready') or ready.get('revision') != REVISION:
                raise RuntimeError('The pinned LocateAnything runtime is unavailable.')
            # Warm kernels on the bundled sample, never a live camera feed.
            self._infer(sample)
            self.info.update(available=True, error=None)
            self.worker = threading.Thread(target=self._run, name='locate-posture', daemon=True)
            self.worker.start()
        except Exception as exc:
            log.warning('LocateAnything setup failed (%s). Object counting remains available.', type(exc).__name__)
            self.info.update(available=False, error='LocateAnything could not start. Check the local WSL runtime and locate-runtime.stderr.log.')
            self._stop_process()

    def _read(self):
        try:
            for line in self.process.stdout:
                if len(line) > 1024 * 1024:
                    raise ValueError('Oversized model response')
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError('Invalid model response')
                self.messages.put_nowait(value)
        except (OSError, ValueError, queue.Full):
            pass
        finally:
            try:
                self.messages.put_nowait({'error': 'LocateAnything worker stopped.'})
            except queue.Full:
                pass

    def _infer(self, frame):
        import cv2
        from .backends import parse_locate
        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise ValueError('Could not encode posture frame')
        self.sequence += 1
        request_id = self.sequence
        self.process.stdin.write(json.dumps(dict(id=request_id,
            jpeg=base64.b64encode(encoded).decode('ascii'))) + '\n')
        self.process.stdin.flush()
        response = self.messages.get(timeout=20)
        if response.get('id') != request_id or response.get('error'):
            raise RuntimeError('LocateAnything inference failed.')
        answer = response.get('answer')
        if not isinstance(answer, str) or len(answer) > 100000:
            raise ValueError('Invalid LocateAnything output')
        return parse_locate(answer, ['standing person', 'sitting person']), response.get('analysis_ms')

    def estimate(self, frame, tracked, raw, captured_at_ms, context=None, input_age_ms=0, blocking=False):
        context = tuple(context or ('preview', 0))
        now = self.clock()
        with self.condition:
            if self.closed or not self.info['available']:
                return seating_evidence(tracked, raw, [], captured_at_ms, False, self.info.get('error'))
            prior = self.results.get(context)
            active_capture = self.active['capture'] if self.active and self.active['context'] == context else None
            pending_capture = self.pending['capture'] if self.pending and self.pending['context'] == context else None
            if captured_at_ms not in (active_capture, pending_capture, prior['capture'] if prior else None):
                # At most one in-flight and one replaceable pending frame.
                self.pending = dict(context=context, capture=captured_at_ms, frame=frame.copy(),
                                    tracked=copy.deepcopy(tracked), raw=copy.deepcopy(raw),
                                    submitted=now, input_age=max(0, input_age_ms), blocking=blocking)
                self.condition.notify_all()
            if blocking:
                self.condition.wait_for(lambda: self.closed or not self.info['available'] or
                    (context in self.results and self.results[context]['capture'] == captured_at_ms), timeout=22)
            if self.closed or not self.info['available']:
                return seating_evidence(tracked, raw, [], captured_at_ms, False, self.info.get('error'))
            prior = self.results.get(context)
            if prior and (not blocking or prior['capture'] == captured_at_ms):
                summary = copy.deepcopy(prior['summary'])
                summary['age_ms'] = round(max(0, (self.clock() - prior['submitted']) * 1000) + prior['input_age'], 1)
                return summary
            if blocking:
                return seating_evidence(tracked, raw, [], captured_at_ms, False,
                    'LocateAnything did not finish this image within the allowed time. Try again.')
            summary = seating_evidence(tracked, raw, [], captured_at_ms, False,
                                      'LocateAnything is analyzing a recent frame; person counting continues.')
            summary.update(status='analyzing', source_session_id=context[0], door_generation=context[1])
            return summary

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                work, self.pending = self.pending, None
                self.active = work
            try:
                classified, analysis_ms = self._infer(work['frame'])
                tracked, raw = work['tracked'], work['raw']
                if work['blocking']:
                    # Still-image inspection uses local identities, never live
                    # cabin clearance. The controller excludes image sources.
                    tracked = [dict(row.json() if hasattr(row, 'json') else row,
                                    track_id=index + 1) for index, row in enumerate(raw)]
                summary = seating_evidence(tracked, raw, classified, work['capture'])
                summary.update(posture_frame_id=self.sequence, source_session_id=work['context'][0],
                               door_generation=work['context'][1], analysis_ms=analysis_ms)
                with self.condition:
                    if not work.get('canceled') and not self.closed:
                        self.results[work['context']] = dict(summary=summary, submitted=work['submitted'],
                            input_age=work['input_age'], capture=work['capture'])
                        self.results.move_to_end(work['context'])
                        while len(self.results) > 4:
                            self.results.popitem(last=False)
                    self.info['last_inference_ms'] = analysis_ms
            except Exception as exc:
                log.warning('LocateAnything inference stopped (%s).', type(exc).__name__)
                with self.condition:
                    self.results.clear()
                    self.pending = None
                    self.info.update(available=False, error='LocateAnything stopped; posture is unknown. Restart the server to reload it.')
                self._stop_process()
                return
            finally:
                with self.condition:
                    self.active = None
                    self.condition.notify_all()

    def invalidate(self, session_id):
        with self.condition:
            if self.pending and self.pending['context'][0] == session_id:
                self.pending = None
            if self.active and self.active['context'][0] == session_id:
                self.active['canceled'] = True
            for context in list(self.results):
                if context[0] == session_id:
                    del self.results[context]
            self.condition.notify_all()

    def _stop_process(self):
        process = self.process
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()  # EOF allows the Linux process to exit cleanly.
                process.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            if process.stdout:
                process.stdout.close()
        if self.log_file:
            self.log_file.close()

    def close(self):
        with self.condition:
            self.closed = True
            self.pending = None
            self.results.clear()
            self.condition.notify_all()
        if self.worker:
            self.worker.join(timeout=22)
        self._stop_process()
