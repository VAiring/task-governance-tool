"""Project worker that records receipt without another LLM control call."""

import time

from .review_wait_direct import DirectProbe


class ManagedDirectProbe(DirectProbe):
    def _run(self, host, record, monotonic_deadline):
        super()._run(host, record, monotonic_deadline)
        # Sending remains one-shot. Only observation follows its settlement;
        # unknown transport outcome can coexist with an independently seen event.
        deadline = min(monotonic_deadline, time.monotonic() + 120)
        try:
            while not self._stop.is_set() and time.monotonic() < deadline:
                current = self.repository.read()
                if current.status not in {"accepted", "unknown"} or current.acknowledged_turn is not None:
                    return
                turn = host.observe_receipt(record.probe_id, record.original_parent_turn)
                if turn is not None:
                    self.repository.update(record.probe_id, acknowledged_turn=turn)
                    return
                if self._stop.wait(self._cycle):
                    return
        except Exception:
            return  # Accepted stays accepted; an unavailable read is not receipt.
