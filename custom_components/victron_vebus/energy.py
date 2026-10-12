"""Energy counters integrated from the power readings of the device.

VE.Bus devices have no energy counters that can be read over the MK3 (a GX
device computes them the same way), so the energy is the integral of the
filtered power readings over time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WATT_SECONDS_PER_KWH = 3_600_000


@dataclass(slots=True)
class EnergyCounter:
    """Integrates power in one direction into energy, with the trapezoidal rule.

    ``direction`` is +1 to count positive power and -1 to count negative
    power; power in the other direction counts as zero. Samples further apart
    than ``max_gap`` seconds are not integrated, so a period without data
    (device unreachable, Home Assistant stopped) adds nothing.
    """

    direction: int
    max_gap: float
    total: float = 0.0  # kWh
    _last: tuple[float, float] | None = field(default=None, repr=False)

    def add(self, timestamp: float, power: float | None) -> bool:
        """Add a power sample in W taken at ``timestamp`` (monotonic seconds).

        Returns True if the total changed.
        """
        if power is None:
            self._last = None
            return False
        power = max(self.direction * power, 0.0)
        last = self._last
        if last is not None and timestamp <= last[0]:
            # The same snapshot published again (e.g. after a command).
            return False
        self._last = (timestamp, power)
        if last is None:
            return False
        elapsed = timestamp - last[0]
        if elapsed > self.max_gap:
            return False
        energy = (last[1] + power) / 2 * elapsed / WATT_SECONDS_PER_KWH
        if energy <= 0:
            return False
        self.total += energy
        return True

    def reset(self) -> None:
        """Forget the last sample, e.g. while the device is unreachable."""
        self._last = None
