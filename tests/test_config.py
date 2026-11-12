from __future__ import annotations

import json
from pathlib import Path

from resource_monitor.config import (
    PrinterConfig,
    Settings,
    load_settings,
    save_settings,
    validate_printer,
)


def test_first_run_has_no_printers(tmp_path: Path):
    settings, warnings = load_settings(tmp_path / "config.json")
    assert settings.printers == []
    assert warnings == []


def test_settings_round_trip(tmp_path: Path):
    path = tmp_path / "nested" / "config.json"
    original = Settings(
        refresh_seconds=120,
        auto_refresh=False,
        ream_size=250,
        printers=[
            PrinterConfig(name="Front office", host="192.0.2.10", asset_tag="A-1"),
            PrinterConfig(
                name="Warehouse",
                host="printer.example",
                community="monitor",
                port=1161,
                show_on_startup=False,
            ),
        ],
    )
    save_settings(original, path)
    loaded, warnings = load_settings(path)
    assert loaded == original
    assert warnings == []
    assert not path.with_name("config.json.tmp").exists()


def test_invalid_printers_are_skipped_with_a_warning(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "refresh_seconds": "soon",
                "printers": [
                    {"name": "Good", "host": "192.0.2.10"},
                    {"name": "", "host": "192.0.2.11"},
                    {"name": "Bad host", "host": "not a host!"},
                    {"name": "good", "host": "192.0.2.12"},
                    "nonsense",
                ],
            }
        ),
        encoding="utf-8",
    )
    settings, warnings = load_settings(path)
    assert [p.name for p in settings.printers] == ["Good"]
    assert settings.refresh_seconds == Settings().refresh_seconds
    assert len(warnings) == 4


def test_unreadable_file_is_kept_as_backup(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")
    settings, warnings = load_settings(path)
    assert settings == Settings()
    assert len(warnings) == 1
    assert not path.exists()
    backups = list(tmp_path.glob("config.unreadable-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{ this is not json"


def test_validate_printer():
    good = PrinterConfig(name="Lobby", host="2001:db8::10")
    assert validate_printer(good, ["Other"]) == []
    assert validate_printer(PrinterConfig(name="Lobby", host="printer-1.example"), []) == []

    problems = validate_printer(
        PrinterConfig(name=" ", host="", community="", port=0, web_url="printer"), []
    )
    assert len(problems) == 5
    assert validate_printer(good, ["LOBBY"]) == [
        'Another printer is already named "Lobby".'
    ]


def test_web_url_defaults_to_host():
    assert (
        PrinterConfig(name="A", host="192.0.2.10").resolved_web_url == "http://192.0.2.10/"
    )
    assert PrinterConfig(name="A", host="2001:db8::10").resolved_web_url == (
        "http://[2001:db8::10]/"
    )
    custom = PrinterConfig(name="A", host="192.0.2.10", web_url="https://p.example/status")
    assert custom.resolved_web_url == "https://p.example/status"
