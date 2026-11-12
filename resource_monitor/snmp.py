"""Read printer status over SNMP using the standard Printer MIB (RFC 3805).

Nothing here touches the user interface. ``read_status`` works with any
object that offers ``get`` and ``walk``, which keeps it testable without a
real printer.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .config import PrinterConfig, Settings
from .models import PrinterStatus, Supply, Tray

log = logging.getLogger(__name__)

OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_HR_DEVICE_DESCR = "1.3.6.1.2.1.25.3.2.1.3.1"
OID_PRINTER_NAME = "1.3.6.1.2.1.43.5.1.1.16.1"
OID_SERIAL = "1.3.6.1.2.1.43.5.1.1.17.1"
OID_INPUT_MAX = "1.3.6.1.2.1.43.8.2.1.9.1"
OID_INPUT_CURRENT = "1.3.6.1.2.1.43.8.2.1.10.1"
OID_INPUT_NAME = "1.3.6.1.2.1.43.8.2.1.13.1"
OID_SUPPLY_TYPE = "1.3.6.1.2.1.43.11.1.1.5.1"
OID_SUPPLY_DESCR = "1.3.6.1.2.1.43.11.1.1.6.1"
OID_SUPPLY_MAX = "1.3.6.1.2.1.43.11.1.1.8.1"
OID_SUPPLY_LEVEL = "1.3.6.1.2.1.43.11.1.1.9.1"
OID_ALERT_DESCR = "1.3.6.1.2.1.43.18.1.1.8.1"

#: prtMarkerSuppliesType values that are waste containers, not consumables.
#: wasteToner, wasteInk, wasteWax, wasteWater, wastePaper.
WASTE_SUPPLY_TYPES = frozenset({4, 8, 14, 24, 26})

SUPPLY_COLORS = ("black", "cyan", "magenta", "yellow")

#: Model names with a bundled picture. Keys are lower case.
BUILTIN_MODEL_IMAGES = {
    "mp c3504ex": "c3504ex.png",
    "mp c6004ex": "c6004ex.png",
    "mp c6503": "c6503.png",
    "im c4500": "c4500.png",
}
MODEL_WITH_LCT_IMAGE = {"mp c6503": "c6503f.png"}
MISSING_MODEL_IMAGE = "missing.png"


class NoSuchObject(Exception):
    """The printer answered, but it does not have the requested value."""


class SnmpSession(Protocol):
    def get(self, oid: str) -> Any:
        """Return one value. Raise NoSuchObject when the printer lacks it."""

    def walk(self, oid: str) -> dict[int, Any]:
        """Return the direct children of ``oid`` keyed by their row index."""


def to_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00 \t\r\n")
    return "" if value is None else str(value).strip()


def to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def detect_color(description: str) -> str | None:
    lowered = description.lower()
    for color in SUPPLY_COLORS:
        if color in lowered:
            return color
    return None


def parse_supplies(
    descriptions: dict[int, Any],
    types: dict[int, Any],
    maximums: dict[int, Any],
    levels: dict[int, Any],
) -> list[Supply]:
    """Combine the supply columns into rows, leaving out waste containers."""
    supplies = []
    for index in sorted(levels):
        name = to_text(descriptions.get(index)) or f"Supply {index}"
        if to_int(types.get(index), 0) in WASTE_SUPPLY_TYPES or "waste" in name.lower():
            continue
        supplies.append(
            Supply(
                name=name,
                level=to_int(levels.get(index), -2),
                max_capacity=to_int(maximums.get(index), -2),
                color=detect_color(name),
            )
        )
    return supplies


def parse_trays(
    names: dict[int, Any], maximums: dict[int, Any], currents: dict[int, Any]
) -> list[Tray]:
    """Combine the input tray columns into rows, leaving out bypass trays."""
    trays = []
    for index in sorted(set(maximums) | set(currents)):
        name = to_text(names.get(index)).replace("Paper", "").strip() or f"Tray {index}"
        if "bypass" in name.lower():
            continue
        trays.append(
            Tray(
                name=name,
                current=to_int(currents.get(index), -2),
                max_capacity=to_int(maximums.get(index), -2),
            )
        )
    return trays


def _first_text(session: SnmpSession, oids: list[str]) -> str:
    for oid in oids:
        try:
            text = to_text(session.get(oid))
        except NoSuchObject:
            continue
        if text:
            return text
    return ""


def read_status(session: SnmpSession) -> PrinterStatus:
    """Query one printer. Connection problems are raised to the caller."""
    model = _first_text(session, [OID_PRINTER_NAME, OID_HR_DEVICE_DESCR, OID_SYS_DESCR])
    serial = _first_text(session, [OID_SERIAL])
    supplies = parse_supplies(
        descriptions=session.walk(OID_SUPPLY_DESCR),
        types=session.walk(OID_SUPPLY_TYPE),
        maximums=session.walk(OID_SUPPLY_MAX),
        levels=session.walk(OID_SUPPLY_LEVEL),
    )
    trays = parse_trays(
        names=session.walk(OID_INPUT_NAME),
        maximums=session.walk(OID_INPUT_MAX),
        currents=session.walk(OID_INPUT_CURRENT),
    )
    alerts = [
        text
        for _, value in sorted(session.walk(OID_ALERT_DESCR).items())
        if (text := to_text(value))
    ]
    return PrinterStatus(
        model=model, serial=serial, supplies=supplies, trays=trays, alerts=alerts
    )


def select_model_image(
    model: str, tray_names: list[str], assets: Path, user_images: Path | None = None
) -> Path:
    """Pick the picture for a printer model.

    A PNG named after the model in the user's images folder wins. Next come
    the bundled pictures. Anything else gets the generic placeholder.
    """
    key = model.strip().lower()
    if user_images is not None and key:
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in model.strip())
        candidate = user_images / f"{safe_name}.png"
        if candidate.is_file():
            return candidate
    has_lct = any("lct" in name.lower() for name in tray_names)
    if has_lct and key in MODEL_WITH_LCT_IMAGE:
        return assets / MODEL_WITH_LCT_IMAGE[key]
    return assets / BUILTIN_MODEL_IMAGES.get(key, MISSING_MODEL_IMAGE)


def child_index(base_oid: str, oid: Any) -> int | None:
    """Row index of ``oid`` when it is a direct child of ``base_oid``."""
    base = base_oid.strip(".")
    full = str(oid).strip(".")
    if not full.startswith(base + "."):
        return None
    remainder = full[len(base) + 1 :]
    return int(remainder) if remainder.isdigit() else None


class PuresnmpSession:
    """SNMP v2c session backed by the ``puresnmp`` library."""

    def __init__(
        self,
        host: str,
        community: str = "public",
        port: int = 161,
        timeout: int = 2,
        retries: int = 2,
        sender: Callable[..., Any] | None = None,
    ) -> None:
        from puresnmp import V2C, Client, PyWrapper

        extra = {} if sender is None else {"sender": sender}
        client = Client(host, V2C(community), port=port, **extra)
        client.configure(timeout=timeout, retries=retries)
        self._snmp = PyWrapper(client)
        self._loop = asyncio.new_event_loop()

    def __enter__(self) -> PuresnmpSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._loop.close()

    def get(self, oid: str) -> Any:
        from puresnmp.exc import NoSuchOID

        try:
            value = self._loop.run_until_complete(self._snmp.get(oid))
        except NoSuchOID as exc:
            raise NoSuchObject(oid) from exc
        if value is None:
            raise NoSuchObject(oid)
        return value

    def walk(self, oid: str) -> dict[int, Any]:
        async def collect() -> list[Any]:
            return [varbind async for varbind in self._snmp.walk(oid)]

        rows: dict[int, Any] = {}
        for varbind in self._loop.run_until_complete(collect()):
            index = child_index(oid, varbind.oid)
            if index is not None:
                rows[index] = varbind.value
        return rows


def describe_error(exc: BaseException) -> str:
    """Turn an exception into a short message for the user."""
    if isinstance(exc, socket.gaierror):
        return "Host name could not be found."
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or (
        type(exc).__name__ == "Timeout"
    ):
        return "No response. The printer is off, unreachable, or SNMP is disabled."
    if isinstance(exc, OSError):
        return f"Network error. {exc.strerror or exc}"
    return str(exc) or type(exc).__name__


def poll_printer(
    printer: PrinterConfig,
    settings: Settings,
    session_factory: Callable[..., Any] = PuresnmpSession,
) -> PrinterStatus:
    """Poll one printer and never raise. Failures come back as ``error``."""
    try:
        with session_factory(
            printer.host,
            community=printer.community,
            port=printer.port,
            timeout=settings.snmp_timeout,
            retries=settings.snmp_retries,
        ) as session:
            return read_status(session)
    except Exception as exc:  # noqa: BLE001 - any failure becomes a status
        log.exception("Polling %s (%s) failed", printer.name, printer.host)
        return PrinterStatus(error=describe_error(exc))
