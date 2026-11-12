"""Launcher. Run with ``python printer_monitor.py``.

The application code lives in the ``resource_monitor`` package.
"""

import sys

from resource_monitor.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
