"""One model owner, bounded pending work, interactive priority with fairness."""

import logging
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


class BusyError(Exception):
    pass


class SupersededError(Exception):
    pass


@dataclass
class Work:
    key: str
    call: object
    future: Future = field(default_factory=Future)
    created: float = field(default_factory=time.perf_counter)
    canceled: threading.Event = field(default_factory=threading.Event)


class Dispatcher:
    def __init__(self, factory, capacity=12):
        self.factory = factory
        self.capacity = capacity
        self.condition = threading.Condition()
        self.interactive = deque()
        self.background = deque()
        self.active = None
        self.state = "starting"
        self.error = None
        self.backend = None
        self.stopping = False
        self.thread = threading.Thread(target=self._run, name="vision-gpu", daemon=True)

    def start(self):
        self.thread.start()

    def submit(self, key, call, interactive=True):
        work = Work(key, call)
        with self.condition:
            if self.stopping or self.state != "ready":
                raise BusyError(self.error or "The model is still loading.")
            for queue in (self.interactive, self.background):
                for old in list(queue):
                    if old.key == key:
                        queue.remove(old)
                        self._discard(old)
            if len(self.interactive) + len(self.background) >= self.capacity:
                raise BusyError("The inference queue is full. Try again shortly.")
            (self.interactive if interactive else self.background).append(work)
            self.condition.notify()
        return work

    @staticmethod
    def _discard(work):
        work.canceled.set()
        if not work.future.done() and not work.future.running():
            work.future.set_exception(SupersededError("Request canceled or replaced."))

    def cancel(self, key):
        with self.condition:
            for queue in (self.interactive, self.background):
                for work in list(queue):
                    if work.key == key:
                        queue.remove(work)
                        self._discard(work)
            if self.active and self.active.key == key:
                self.active.canceled.set()

    def status(self):
        with self.condition:
            return {"state": self.state, "error": self.error,
                    "pending": len(self.interactive) + len(self.background),
                    "busy": self.active is not None,
                    "backend": self.backend.info if self.backend else None}

    def close(self):
        with self.condition:
            self.stopping = True
            for queue in (self.interactive, self.background):
                while queue:
                    self._discard(queue.popleft())
            if self.active:
                self.active.canceled.set()
            self.condition.notify_all()
        self.thread.join(timeout=35)

    def _run(self):
        try:
            self.backend = self.factory()
            self.backend.warmup()
            self.state = "ready"
        except Exception as exc:
            log.exception("Model startup failed")
            self.error = str(exc)
            self.state = "error"
            if self.backend and hasattr(self.backend, 'close'):
                self.backend.close()
            return
        streak = 0
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stopping or self.interactive or self.background)
                if self.stopping:
                    break
                if self.background and (not self.interactive or streak >= 4):
                    work = self.background.popleft()
                    streak = 0
                else:
                    work = self.interactive.popleft()
                    streak += 1
                self.active = work
            try:
                if not work.future.set_running_or_notify_cancel():
                    continue
                if work.canceled.is_set():
                    raise SupersededError("Request canceled.")
                queue_ms = (time.perf_counter() - work.created) * 1000
                result = work.call(self.backend, work.canceled, queue_ms)
                if work.canceled.is_set():
                    raise SupersededError("Request canceled.")
                work.future.set_result(result)
            except Exception as exc:
                if not work.future.done():
                    work.future.set_exception(exc)
            finally:
                with self.condition:
                    self.active = None
        if hasattr(self.backend, 'close'):
            self.backend.close()
        self.state = "stopped"
