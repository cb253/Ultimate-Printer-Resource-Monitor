"""Program entry point. Run with ``python -m resource_monitor``."""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from logging.handlers import RotatingFileHandler

from . import paths
from .config import load_settings
from .gui import App


def configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        log_file = paths.log_path()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
        )
    except OSError:
        pass  # Console logging still works when the folder is not writable.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> int:
    configure_logging()
    settings, warnings = load_settings(paths.config_path())
    root = tk.Tk()
    App(root, settings, paths.config_path(), warnings)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
