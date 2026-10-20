"""Quality of the link between the MK3 interface and the VE.Bus device."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

# Requests needed in the window before a rate is reported.
MIN_REQUESTS = 20


@dataclass(slots=True)
class LinkQuality:
    """Share of requests that got a reply, over a sliding time window.

    Samples are the client's running request and timeout counters, so the
    rate covers every request in the window: polls and commands alike.
    """

    window: float
    _samples: deque[tuple[float, int, int]] = field(default_factory=deque)

    def add(self, timestamp: float, requests: int, timeouts: int) -> None:
        """Add the counters as they are at ``timestamp`` (monotonic seconds)."""
        if self._samples and requests < self._samples[-1][1]:
            # The counters were reset (new client): start over.
            self._samples.clear()
        self._samples.append((timestamp, requests, timeouts))
        while len(self._samples) > 1 and self._samples[0][0] < timestamp - self.window:
            self._samples.popleft()

    @property
    def requests(self) -> int:
        """Return the number of requests in the window."""
        if not self._samples:
            return 0
        return self._samples[-1][1] - self._samples[0][1]

    @property
    def timeouts(self) -> int:
        """Return the number of requests in the window that got no reply."""
        if not self._samples:
            return 0
        return self._samples[-1][2] - self._samples[0][2]

    @property
    def success_rate(self) -> float | None:
        """Return the percentage of answered requests, None if too few."""
        requests = self.requests
        if requests < MIN_REQUESTS:
            return None
        return round(100 * (requests - self.timeouts) / requests, 1)
