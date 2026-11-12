"""Printer list and settings, stored as JSON in the user's data folder."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_VERSION = 1

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


@dataclass
class PrinterConfig:
    """One printer the user wants to monitor."""

    name: str
    host: str
    community: str = "public"
    port: int = 161
    serial: str = ""
    asset_tag: str = ""
    show_on_startup: bool = True
    web_url: str = ""

    @property
    def resolved_web_url(self) -> str:
        """Address of the printer's web page."""
        if self.web_url:
            return self.web_url
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}/"


@dataclass
class Settings:
    refresh_seconds: int = 600
    auto_refresh: bool = True
    ream_size: int = 500
    low_supply_percent: int = 20
    snmp_timeout: int = 2
    snmp_retries: int = 2
    printers: list[PrinterConfig] = field(default_factory=list)


def is_valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(_HOSTNAME_RE.match(host))


def validate_printer(printer: PrinterConfig, other_names: list[str]) -> list[str]:
    """Return a list of problems. An empty list means the printer is valid."""
    problems = []
    if not printer.name.strip():
        problems.append("Name is required.")
    elif printer.name.strip().casefold() in (n.strip().casefold() for n in other_names):
        problems.append(f'Another printer is already named "{printer.name.strip()}".')
    if not printer.host.strip():
        problems.append("IP address or host name is required.")
    elif not is_valid_host(printer.host.strip()):
        problems.append(f'"{printer.host}" is not a valid IP address or host name.')
    if not printer.community:
        problems.append("SNMP community is required.")
    if not 1 <= printer.port <= 65535:
        problems.append("Port must be between 1 and 65535.")
    if printer.web_url and not printer.web_url.startswith(("http://", "https://")):
        problems.append("Web page address must start with http:// or https://.")
    return problems


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if minimum <= number <= maximum else default


def _printer_from_dict(raw: Any) -> PrinterConfig | None:
    if not isinstance(raw, dict):
        return None
    printer = PrinterConfig(
        name=str(raw.get("name", "")).strip(),
        host=str(raw.get("host", "")).strip(),
        community=str(raw.get("community", "public")),
        port=_as_int(raw.get("port"), 161, 1, 65535),
        serial=str(raw.get("serial", "")).strip(),
        asset_tag=str(raw.get("asset_tag", "")).strip(),
        show_on_startup=bool(raw.get("show_on_startup", True)),
        web_url=str(raw.get("web_url", "")).strip(),
    )
    return printer


def settings_from_dict(raw: dict[str, Any]) -> tuple[Settings, list[str]]:
    """Build Settings from parsed JSON. Returns the settings and any warnings."""
    warnings: list[str] = []
    defaults = Settings()
    settings = Settings(
        refresh_seconds=_as_int(
            raw.get("refresh_seconds"), defaults.refresh_seconds, 5, 86400
        ),
        auto_refresh=bool(raw.get("auto_refresh", defaults.auto_refresh)),
        ream_size=_as_int(raw.get("ream_size"), defaults.ream_size, 1, 100000),
        low_supply_percent=_as_int(
            raw.get("low_supply_percent"), defaults.low_supply_percent, 0, 100
        ),
        snmp_timeout=_as_int(raw.get("snmp_timeout"), defaults.snmp_timeout, 1, 60),
        snmp_retries=_as_int(raw.get("snmp_retries"), defaults.snmp_retries, 1, 10),
    )
    raw_printers = raw.get("printers", [])
    if not isinstance(raw_printers, list):
        warnings.append('"printers" is not a list, so it was ignored.')
        raw_printers = []
    for position, entry in enumerate(raw_printers, start=1):
        printer = _printer_from_dict(entry)
        problems = (
            ["Entry is not an object."]
            if printer is None
            else validate_printer(printer, [p.name for p in settings.printers])
        )
        if printer is None or problems:
            warnings.append(f"Printer entry {position} was skipped. {' '.join(problems)}")
            continue
        settings.printers.append(printer)
    return settings, warnings


def settings_to_dict(settings: Settings) -> dict[str, Any]:
    data = asdict(settings)
    return {"version": CONFIG_VERSION, **data}


def load_settings(path: Path) -> tuple[Settings, list[str]]:
    """Read the config file.

    A missing file yields default settings with no printers. An unreadable
    file is kept as a backup copy and defaults are used instead.
    """
    if not path.exists():
        return Settings(), []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("top level of the file is not an object")
    except (OSError, ValueError) as exc:
        log.exception("Could not read config file %s", path)
        warning = f"The config file could not be read ({exc})."
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = path.with_name(f"{path.stem}.unreadable-{stamp}{path.suffix}")
            os.replace(path, backup)
            warning += f" It was kept as {backup.name} and default settings were loaded."
        except OSError:
            log.exception("Could not back up unreadable config file")
            warning += " Default settings were loaded."
        return Settings(), [warning]
    return settings_from_dict(raw)


def save_settings(settings: Settings, path: Path) -> None:
    """Write the config file without risking a half-written result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(settings_to_dict(settings), indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
