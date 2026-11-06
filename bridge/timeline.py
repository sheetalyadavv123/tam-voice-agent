"""How long after /sampark each step of a call happened, logged once per step."""
import logging
import time

log = logging.getLogger("bridge")


class Timeline:
    def __init__(self, call_number, t0=None):
        self.call_number = call_number
        self.t0 = time.monotonic() if t0 is None else t0  # when /sampark was called
        self.seen = set()

    def mark(self, step):
        if step in self.seen:
            return
        self.seen.add(step)
        log.info("timing %s: %s +%.2fs since /sampark",
                 self.call_number, step, time.monotonic() - self.t0)
