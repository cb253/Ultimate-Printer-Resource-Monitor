from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fakes import PRINTER_VALUES, FakeSession, make_fake_agent

from resource_monitor import snmp
from resource_monitor.config import PrinterConfig, Settings
from resource_monitor.models import Supply, Tray

PRINTER = PrinterConfig(name="Test printer", host="192.0.2.10")


def check_printer_status(status):
    assert status.ok
    assert status.model == "MP C6503"
    assert status.serial == "TEST0000001"
    assert [s.name for s in status.supplies] == [
        "Black Toner",
        "Cyan Toner",
        "Magenta Toner",
        "Yellow Toner",
    ]
    assert [s.color for s in status.supplies] == ["black", "cyan", "magenta", "yellow"]
    assert [s.status_text for s in status.supplies] == ["80%", "15%", "OK", "?"]
    assert [t.name for t in status.trays] == ["Tray 1", "Tray 2", "Tray 3 (LCT)"]
    assert status.paper_deficit == 495
    assert status.paper_capacity == 1100
    assert status.paper_percent == 55
    assert status.alerts == ["Paper Tray 1 almost empty"]


def test_read_status_from_fake_session():
    check_printer_status(snmp.read_status(FakeSession(PRINTER_VALUES)))


def test_read_status_through_real_snmp_library():
    with snmp.PuresnmpSession(
        "192.0.2.10", sender=make_fake_agent(PRINTER_VALUES)
    ) as session:
        check_printer_status(snmp.read_status(session))


def test_real_session_reports_missing_values():
    with snmp.PuresnmpSession("192.0.2.10", sender=make_fake_agent({})) as session:
        with pytest.raises(snmp.NoSuchObject):
            session.get(snmp.OID_SERIAL)
        assert session.walk(snmp.OID_SUPPLY_LEVEL) == {}


def test_model_falls_back_to_system_description():
    values = {snmp.OID_SYS_DESCR: b"Generic Laser 9000"}
    status = snmp.read_status(FakeSession(values))
    assert status.model == "Generic Laser 9000"
    assert status.serial == ""
    assert status.supplies == []
    assert status.trays == []
    assert status.paper_percent is None


def test_supply_rows_are_matched_by_index_not_position():
    supplies = snmp.parse_supplies(
        descriptions={7: b"Cyan Cartridge", 3: b"Black Cartridge"},
        types={3: 3, 7: 3},
        maximums={3: 2000, 7: 1000},
        levels={7: 250, 3: 500},
    )
    assert supplies == [
        Supply("Black Cartridge", 500, 2000, "black"),
        Supply("Cyan Cartridge", 250, 1000, "cyan"),
    ]
    assert [s.percent for s in supplies] == [25, 25]


def test_tray_with_zero_capacity_does_not_divide_by_zero():
    tray = Tray("Tray 1", current=0, max_capacity=0)
    assert tray.percent is None
    assert tray.deficit == 0


def test_poll_printer_turns_timeout_into_error_status():
    def factory(host, **kwargs):
        return FakeSession({}, fail_with=TimeoutError("no answer"))

    status = snmp.poll_printer(PRINTER, Settings(), session_factory=factory)
    assert not status.ok
    assert "No response" in status.error


def test_poll_printer_reports_unknown_host():
    def factory(host, **kwargs):
        raise socket.gaierror(11001, "getaddrinfo failed")

    status = snmp.poll_printer(PRINTER, Settings(), session_factory=factory)
    assert status.error == "Host name could not be found."


def test_poll_printer_passes_connection_settings():
    seen = {}

    def factory(host, **kwargs):
        seen.update(host=host, **kwargs)
        return FakeSession(PRINTER_VALUES)

    printer = PrinterConfig(name="P", host="printer.example", community="c", port=1161)
    settings = Settings(snmp_timeout=5, snmp_retries=3)
    assert snmp.poll_printer(printer, settings, session_factory=factory).ok
    assert seen == {
        "host": "printer.example",
        "community": "c",
        "port": 1161,
        "timeout": 5,
        "retries": 3,
    }


def test_select_model_image(tmp_path: Path):
    assets = Path("assets")
    assert snmp.select_model_image("MP C6004ex", [], assets) == assets / "c6004ex.png"
    assert snmp.select_model_image("MP C6503", ["Tray 1"], assets) == assets / "c6503.png"
    assert (
        snmp.select_model_image("MP C6503", ["Tray 3 (LCT)"], assets)
        == assets / "c6503f.png"
    )
    assert snmp.select_model_image("Other 100", [], assets) == assets / "missing.png"

    custom = tmp_path / "Other 100.png"
    custom.write_bytes(b"")
    assert snmp.select_model_image("Other 100", [], assets, tmp_path) == custom


def test_bundled_model_images_exist():
    from resource_monitor.paths import assets_dir

    names = {
        *snmp.BUILTIN_MODEL_IMAGES.values(),
        *snmp.MODEL_WITH_LCT_IMAGE.values(),
        snmp.MISSING_MODEL_IMAGE,
        "NoConnection.png",
        "icon.png",
        "icon.ico",
    }
    for name in names:
        assert (assets_dir() / name).is_file(), name
