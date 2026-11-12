"""Where the application keeps its files."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import APP_DIR_NAME

#: Set this environment variable to keep config and logs somewhere else.
DATA_DIR_ENV = "UPRM_DATA_DIR"


def data_dir() -> Path:
    """Per-user folder for the config file, logs, and custom images."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR_NAME


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "logs" / "monitor.log"


def user_images_dir() -> Path:
    return data_dir() / "images"


def assets_dir() -> Path:
    """Images shipped with the application."""
    return Path(__file__).resolve().parent / "assets"


def open_path(path: Path) -> None:
    """Open a file or folder with the operating system's default handler."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - opening a local path the app owns
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
