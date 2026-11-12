"""Stand-ins for a real printer."""

from __future__ import annotations

from typing import Any

from puresnmp.pdu import (
    EndOfMibView,
    GetNextRequest,
    GetResponse,
    PDUContent,
)
from puresnmp.pdu import NoSuchObject as NoSuchObjectMarker
from puresnmp.varbind import VarBind
from x690 import decode
from x690.types import Integer, ObjectIdentifier, OctetString, Sequence

from resource_monitor import snmp

# A color multifunction printer. Addresses and names are invented.
PRINTER_VALUES: dict[str, Any] = {
    snmp.OID_PRINTER_NAME: b"MP C6503",
    snmp.OID_SERIAL: b"TEST0000001",
    # Supplies. Row 2 is the waste toner bottle.
    f"{snmp.OID_SUPPLY_TYPE}.1": 3,
    f"{snmp.OID_SUPPLY_TYPE}.2": 4,
    f"{snmp.OID_SUPPLY_TYPE}.3": 3,
    f"{snmp.OID_SUPPLY_TYPE}.4": 3,
    f"{snmp.OID_SUPPLY_TYPE}.5": 3,
    f"{snmp.OID_SUPPLY_DESCR}.1": b"Black Toner",
    f"{snmp.OID_SUPPLY_DESCR}.2": b"Waste Toner",
    f"{snmp.OID_SUPPLY_DESCR}.3": b"Cyan Toner",
    f"{snmp.OID_SUPPLY_DESCR}.4": b"Magenta Toner",
    f"{snmp.OID_SUPPLY_DESCR}.5": b"Yellow Toner",
    f"{snmp.OID_SUPPLY_MAX}.1": 100,
    f"{snmp.OID_SUPPLY_MAX}.2": 100,
    f"{snmp.OID_SUPPLY_MAX}.3": 100,
    f"{snmp.OID_SUPPLY_MAX}.4": 100,
    f"{snmp.OID_SUPPLY_MAX}.5": -2,
    f"{snmp.OID_SUPPLY_LEVEL}.1": 80,
    f"{snmp.OID_SUPPLY_LEVEL}.2": -3,
    f"{snmp.OID_SUPPLY_LEVEL}.3": 15,
    f"{snmp.OID_SUPPLY_LEVEL}.4": -3,
    f"{snmp.OID_SUPPLY_LEVEL}.5": -2,
    # Trays. Row 3 reports no sheet count. Row 4 is the bypass tray.
    f"{snmp.OID_INPUT_NAME}.1": b"Paper Tray 1",
    f"{snmp.OID_INPUT_NAME}.2": b"Paper Tray 2",
    f"{snmp.OID_INPUT_NAME}.3": b"Tray 3 (LCT)",
    f"{snmp.OID_INPUT_NAME}.4": b"Bypass Tray",
    f"{snmp.OID_INPUT_MAX}.1": 550,
    f"{snmp.OID_INPUT_MAX}.2": 550,
    f"{snmp.OID_INPUT_MAX}.3": 2000,
    f"{snmp.OID_INPUT_MAX}.4": 100,
    f"{snmp.OID_INPUT_CURRENT}.1": 55,
    f"{snmp.OID_INPUT_CURRENT}.2": 550,
    f"{snmp.OID_INPUT_CURRENT}.3": -3,
    f"{snmp.OID_INPUT_CURRENT}.4": 0,
    f"{snmp.OID_ALERT_DESCR}.1": b"Paper Tray 1 almost empty",
    f"{snmp.OID_ALERT_DESCR}.2": b"",
}


def _oid_key(oid: str) -> tuple[int, ...]:
    return tuple(int(part) for part in oid.strip(".").split("."))


class FakeSession:
    """Implements the SnmpSession protocol from a dictionary."""

    def __init__(self, values: dict[str, Any], fail_with: Exception | None = None):
        self.values = values
        self.fail_with = fail_with

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def get(self, oid: str) -> Any:
        if self.fail_with is not None:
            raise self.fail_with
        if oid not in self.values:
            raise snmp.NoSuchObject(oid)
        return self.values[oid]

    def walk(self, oid: str) -> dict[int, Any]:
        if self.fail_with is not None:
            raise self.fail_with
        rows = {}
        for key, value in self.values.items():
            index = snmp.child_index(oid, key)
            if index is not None:
                rows[index] = value
        return rows


class _EndOfMibView(EndOfMibView):
    def encode_raw(self) -> bytes:
        return b""


class _NoSuchObject(NoSuchObjectMarker):
    def encode_raw(self) -> bytes:
        return b""


def make_fake_agent(values: dict[str, Any]):
    """Return a puresnmp ``sender`` that answers like an SNMP v2c agent.

    This exercises the real request encoding and response decoding without
    any network traffic.
    """
    encoded = {
        oid: OctetString(value) if isinstance(value, bytes) else Integer(value)
        for oid, value in values.items()
    }
    ordered = sorted(encoded, key=_oid_key)

    async def sender(endpoint, packet, timeout=1, loop=None, retries=1) -> bytes:
        message, _ = decode(packet)
        version, community, request = message.value[:3]
        answers = []
        for varbind in request.value.varbinds:
            oid = str(varbind.oid).strip(".")
            if isinstance(request, GetNextRequest):
                following = next((o for o in ordered if _oid_key(o) > _oid_key(oid)), None)
                if following is None:
                    answers.append(VarBind(varbind.oid, _EndOfMibView()))
                else:
                    answers.append(VarBind(ObjectIdentifier(following), encoded[following]))
            elif oid in encoded:
                answers.append(VarBind(varbind.oid, encoded[oid]))
            else:
                answers.append(VarBind(varbind.oid, _NoSuchObject()))
        response = GetResponse(PDUContent(request.value.request_id, answers))
        return bytes(Sequence([version, community, response]))

    return sender
