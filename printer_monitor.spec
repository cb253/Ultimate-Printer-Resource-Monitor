# -*- mode: python -*-
# Build a standalone executable with:  pyinstaller printer_monitor.spec

a = Analysis(
    ["printer_monitor.py"],
    datas=[("resource_monitor/assets", "resource_monitor/assets")],
    hiddenimports=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Ultimate Printer Resource Monitor",
    console=False,
    icon="resource_monitor/assets/icon.ico",
)
