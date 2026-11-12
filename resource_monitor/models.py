"""Plain data objects describing what a printer reported."""

from __future__ import annotations

from dataclasses import dataclass, field

# Printer MIB (RFC 3805) uses negative numbers as markers, not quantities.
LEVEL_OTHER = -1
LEVEL_UNKNOWN = -2
LEVEL_SOME_REMAINING = -3


@dataclass(frozen=True)
class Supply:
    """A consumable such as a toner cartridge."""

    name: str
    level: int
    max_capacity: int
    color: str | None = None

    @property
    def percent(self) -> int | None:
        """Fill percentage, or None when the printer does not report one."""
        if self.max_capacity > 0 and self.level >= 0:
            return max(0, min(100, round(self.level * 100 / self.max_capacity)))
        return None

    @property
    def status_text(self) -> str:
        percent = self.percent
        if percent is not None:
            return f"{percent}%"
        if self.level == LEVEL_SOME_REMAINING:
            return "OK"
        return "?"


@dataclass(frozen=True)
class Tray:
    """A paper input tray."""

    name: str
    current: int
    max_capacity: int

    @property
    def is_measurable(self) -> bool:
        """True when the printer reports real sheet counts for this tray."""
        return self.max_capacity > 0 and self.current >= 0

    @property
    def deficit(self) -> int:
        """Sheets needed to fill the tray. Zero when it cannot be measured."""
        if not self.is_measurable:
            return 0
        return max(0, self.max_capacity - self.current)

    @property
    def percent(self) -> int | None:
        if not self.is_measurable:
            return None
        return max(0, min(100, round(self.current * 100 / self.max_capacity)))


@dataclass
class PrinterStatus:
    """Everything one poll of a printer returned."""

    model: str = ""
    serial: str = ""
    supplies: list[Supply] = field(default_factory=list)
    trays: list[Tray] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def paper_deficit(self) -> int:
        return sum(tray.deficit for tray in self.trays)

    @property
    def paper_capacity(self) -> int:
        return sum(tray.max_capacity for tray in self.trays if tray.is_measurable)

    @property
    def paper_percent(self) -> int | None:
        capacity = self.paper_capacity
        if capacity <= 0:
            return None
        return round((capacity - self.paper_deficit) * 100 / capacity)
