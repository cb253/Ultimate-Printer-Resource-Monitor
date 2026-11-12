"""Tkinter user interface.

All SNMP work happens on background threads. Results travel back through a
queue that the main thread drains, because Tk widgets may only be touched
from the thread that created them.
"""

from __future__ import annotations

import copy
import logging
import math
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from . import APP_NAME, __version__, paths
from .config import PrinterConfig, Settings, save_settings, validate_printer
from .models import PrinterStatus
from .snmp import poll_printer, select_model_image

log = logging.getLogger(__name__)

MAX_CONCURRENT_POLLS = 8
QUEUE_POLL_MS = 100
IMAGE_TARGET_PX = 140
LINK_COLOR = "#0645ad"
ALERT_COLOR = "#c00000"
SUPPLY_BAR_COLORS = {
    "black": "#202020",
    "cyan": "#00b7eb",
    "magenta": "#e6007e",
    "yellow": "#f5d000",
    None: "#7a7a7a",
}


def supply_bar_style(color: str | None) -> str:
    return f"{color or 'other'}.Horizontal.TProgressbar"


class Tooltip:
    """Small text popup shown while the pointer rests on a widget."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _event: tk.Event) -> None:
        if self._window is not None or not self.text:
            return
        x = self.widget.winfo_pointerx() + 14
        y = self.widget.winfo_pointery() + 14
        self._window = window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            window,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            foreground="#000000",
            relief=tk.SOLID,
            borderwidth=1,
            padx=4,
            pady=2,
        ).pack()

    def _hide(self, _event: tk.Event | None = None) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None


class ImageCache:
    """Loads pictures once and scales them down to a common size."""

    def __init__(self) -> None:
        self._images: dict[Path, tk.PhotoImage] = {}

    def get(self, path: Path) -> tk.PhotoImage | None:
        if path not in self._images:
            try:
                image = tk.PhotoImage(file=str(path))
            except tk.TclError:
                log.exception("Could not load image %s", path)
                return None
            factor = math.ceil(max(image.width(), image.height()) / IMAGE_TARGET_PX)
            self._images[path] = image.subsample(factor) if factor > 1 else image
        return self._images[path]


class ScrollableRow(ttk.Frame):
    """Holds the printer cards side by side with a horizontal scrollbar."""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            self, orient=tk.HORIZONTAL, command=self.canvas.xview
        )
        self.canvas.configure(xscrollcommand=self.scrollbar.set)
        self.inner = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner, anchor=tk.NW)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.inner.bind("<Configure>", self._on_inner_resize)

    def _on_inner_resize(self, _event: tk.Event) -> None:
        width = self.inner.winfo_reqwidth()
        height = self.inner.winfo_reqheight()
        self.canvas.configure(scrollregion=(0, 0, width, height), height=height)
        # Grow with the content, up to most of the screen width.
        limit = int(self.winfo_screenwidth() * 0.8)
        self.canvas.configure(width=min(width, limit))


class PrinterCard(ttk.Frame):
    """One printer's column in the main window."""

    def __init__(self, parent: tk.Widget, printer: PrinterConfig, app: App) -> None:
        super().__init__(parent, padding=(8, 4))
        self.printer = printer
        self.app = app
        self.status: PrinterStatus | None = None

        title = ttk.Label(self, text=printer.name, font=("TkDefaultFont", 14))
        title.pack()
        self._title_tip = Tooltip(title, self._identity_text(""))
        self.body = ttk.Frame(self)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.show_loading()

    def _identity_text(self, reported_serial: str) -> str:
        lines = []
        serial = self.printer.serial or reported_serial
        if serial:
            lines.append(f"Serial: {serial}")
        if self.printer.asset_tag:
            lines.append(f"Asset tag: {self.printer.asset_tag}")
        return "\n".join(lines)

    def _clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()

    def show_loading(self) -> None:
        # Keep the previous reading on screen while a refresh is running.
        if self.status is not None and self.status.ok:
            return
        self._clear()
        ttk.Label(self.body, text="Contacting printer...").pack(pady=40, padx=20)

    def show_status(self, status: PrinterStatus) -> None:
        self.status = status
        self._clear()
        if status.ok:
            self._title_tip.text = self._identity_text(status.serial)
            self._render_status(status)
        else:
            self._render_error(status)

    def _link(self, parent: tk.Widget, text: str, action: Callable[[], None]) -> ttk.Label:
        label = ttk.Label(
            parent,
            text=text,
            foreground=LINK_COLOR,
            cursor="hand2",
            font=("TkDefaultFont", 9, "underline"),
        )
        label.bind("<Button-1>", lambda _event: action())
        return label

    def _render_status(self, status: PrinterStatus) -> None:
        settings = self.app.settings
        ttk.Label(self.body, text=status.model or "Unknown model").pack()
        url = self.printer.resolved_web_url
        link = self._link(self.body, self.printer.host, lambda: webbrowser.open(url))
        Tooltip(link, "Open the printer's web page")
        link.pack()

        if status.alerts:
            alert_frame = ttk.Frame(self.body)
            alert_frame.pack(pady=(5, 0))
            alerts = tk.Listbox(
                alert_frame,
                height=min(3, len(status.alerts)),
                width=30,
                foreground=ALERT_COLOR,
                selectforeground=ALERT_COLOR,
                relief=tk.FLAT,
                borderwidth=0,
                highlightthickness=0,
                activestyle="none",
                background=self.app.background,
                selectbackground=self.app.background,
            )
            alerts.insert(tk.END, *status.alerts)
            alerts.pack(side=tk.LEFT)
            if len(status.alerts) > 3:
                scrollbar = ttk.Scrollbar(alert_frame, command=alerts.yview)
                alerts.configure(yscrollcommand=scrollbar.set)
                scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        image = self.app.images.get(
            select_model_image(
                status.model,
                [tray.name for tray in status.trays],
                paths.assets_dir(),
                paths.user_images_dir(),
            )
        )
        if image is not None:
            ttk.Label(self.body, image=image).pack(pady=4)

        supply_frame = ttk.Frame(self.body)
        supply_frame.pack()
        for row, supply in enumerate(status.supplies):
            percent = supply.percent
            low = percent is not None and percent <= settings.low_supply_percent
            bar = ttk.Progressbar(
                supply_frame,
                length=110,
                value=percent or 0,
                style=supply_bar_style(supply.color),
            )
            bar.grid(row=row, column=0, pady=1)
            Tooltip(bar, supply.name)
            value = ttk.Label(
                supply_frame,
                text=supply.status_text,
                width=5,
                anchor=tk.E,
                foreground=ALERT_COLOR if low else "",
            )
            value.grid(row=row, column=1, padx=(4, 0))
            Tooltip(value, supply.name)
        if not status.supplies:
            ttk.Label(supply_frame, text="No supply levels reported").pack()

        tray_frame = ttk.Frame(self.body)
        tray_frame.pack(pady=(10, 5))
        # A tray is flagged once it can take about one full ream.
        ream_threshold = settings.ream_size * 0.99
        for row, tray in enumerate(status.trays):
            color = ALERT_COLOR if tray.deficit >= ream_threshold else ""
            ttk.Label(
                tray_frame,
                text=f"{tray.name}:",
                font=("TkDefaultFont", 9, "bold"),
                foreground=color,
            ).grid(row=row, column=0, sticky=tk.W)
            if tray.is_measurable:
                percent_text = f"{tray.percent}%"
                count_text = f"({tray.current}/{tray.max_capacity})"
            else:
                percent_text = "?"
                count_text = "(not reported)"
            ttk.Label(tray_frame, text=percent_text, foreground=color).grid(
                row=row, column=1, sticky=tk.E, padx=(8, 4)
            )
            ttk.Label(tray_frame, text=count_text, foreground=color).grid(
                row=row, column=2, sticky=tk.E
            )

        paper_percent = status.paper_percent
        if paper_percent is not None:
            low_paper = paper_percent <= 50
            paper_bar = ttk.Progressbar(
                self.body,
                length=165,
                value=paper_percent,
                style="paper.Horizontal.TProgressbar",
            )
            paper_bar.pack()
            paper_label = ttk.Label(
                self.body,
                text=f"Paper fill: {paper_percent}%",
                font=("TkDefaultFont", 8),
                foreground=ALERT_COLOR if low_paper else "",
            )
            paper_label.pack()
            reams = status.paper_deficit / settings.ream_size
            tip = f"{status.paper_deficit} sheets needed ({reams:.1f} reams)"
            Tooltip(paper_bar, tip)
            Tooltip(paper_label, tip)

    def _render_error(self, status: PrinterStatus) -> None:
        image = self.app.images.get(paths.assets_dir() / "NoConnection.png")
        if image is not None:
            ttk.Label(self.body, image=image).pack(pady=(40, 5))
        ttk.Label(self.body, text="No connection", foreground=ALERT_COLOR).pack()
        ttk.Label(
            self.body,
            text=status.error or "",
            foreground=ALERT_COLOR,
            wraplength=180,
            justify=tk.CENTER,
        ).pack(pady=(2, 6))
        ttk.Label(self.body, text=self.printer.host).pack()
        self._link(self.body, "Open log file", self.app.open_log).pack(pady=2)
        ttk.Button(
            self.body, text="Retry", command=lambda: self.app.poll(self.printer.name)
        ).pack(pady=4)


class PrinterForm(tk.Toplevel):
    """Dialog for adding or changing one printer."""

    def __init__(
        self, parent: tk.Misc, printer: PrinterConfig | None, other_names: list[str]
    ) -> None:
        super().__init__(parent)
        self.title("Edit printer" if printer else "Add printer")
        self.resizable(False, False)
        self.transient(parent)
        self.result: PrinterConfig | None = None
        self._other_names = other_names
        source = printer or PrinterConfig(name="", host="")

        self._vars = {
            "name": tk.StringVar(value=source.name),
            "host": tk.StringVar(value=source.host),
            "community": tk.StringVar(value=source.community),
            "port": tk.StringVar(value=str(source.port)),
            "serial": tk.StringVar(value=source.serial),
            "asset_tag": tk.StringVar(value=source.asset_tag),
            "web_url": tk.StringVar(value=source.web_url),
        }
        self._startup = tk.BooleanVar(value=source.show_on_startup)
        labels = [
            ("name", "Name"),
            ("host", "IP address or host name"),
            ("community", "SNMP community"),
            ("port", "SNMP port"),
            ("serial", "Serial number (optional)"),
            ("asset_tag", "Asset tag (optional)"),
            ("web_url", "Web page address (optional)"),
        ]
        form = ttk.Frame(self, padding=12)
        form.pack(fill=tk.BOTH, expand=True)
        first_entry = None
        for row, (key, label) in enumerate(labels):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky=tk.W, pady=3)
            entry = ttk.Entry(form, textvariable=self._vars[key], width=34)
            entry.grid(row=row, column=1, sticky=tk.EW, padx=(10, 0), pady=3)
            first_entry = first_entry or entry
        ttk.Checkbutton(
            form, text="Show when the program starts", variable=self._startup
        ).grid(row=len(labels), column=1, sticky=tk.W, padx=(10, 0), pady=(6, 0))

        buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="OK", command=self._accept).pack(
            side=tk.RIGHT, padx=(0, 6)
        )
        self.bind("<Return>", lambda _event: self._accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        if first_entry is not None:
            first_entry.focus_set()
        self.grab_set()

    def _accept(self) -> None:
        port_text = self._vars["port"].get().strip()
        printer = PrinterConfig(
            name=self._vars["name"].get().strip(),
            host=self._vars["host"].get().strip(),
            community=self._vars["community"].get(),
            port=int(port_text) if port_text.isdigit() else 0,
            serial=self._vars["serial"].get().strip(),
            asset_tag=self._vars["asset_tag"].get().strip(),
            show_on_startup=self._startup.get(),
            web_url=self._vars["web_url"].get().strip(),
        )
        problems = validate_printer(printer, self._other_names)
        if problems:
            messagebox.showerror(
                "Check the printer details", "\n".join(problems), parent=self
            )
            return
        self.result = printer
        self.destroy()


class PrinterEditor(tk.Toplevel):
    """Dialog listing all printers with add, edit, remove, and reorder."""

    def __init__(
        self,
        parent: tk.Misc,
        printers: list[PrinterConfig],
        on_save: Callable[[list[PrinterConfig]], None],
    ) -> None:
        super().__init__(parent)
        self.title("Edit printers")
        self.transient(parent)
        self._printers = copy.deepcopy(printers)
        self._on_save = on_save

        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        columns = ("name", "host", "startup")
        self.tree = ttk.Treeview(
            body, columns=columns, show="headings", height=10, selectmode="browse"
        )
        for column, heading, width in (
            ("name", "Name", 170),
            ("host", "IP address or host name", 200),
            ("startup", "Show at start", 100),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, anchor=tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", lambda _event: self._edit())

        side = ttk.Frame(body)
        side.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        for text, command in (
            ("Add...", self._add),
            ("Edit...", self._edit),
            ("Remove", self._remove),
            ("Move up", lambda: self._move(-1)),
            ("Move down", lambda: self._move(1)),
        ):
            ttk.Button(side, text=text, command=command).pack(fill=tk.X, pady=2)

        buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", command=self._save).pack(
            side=tk.RIGHT, padx=(0, 6)
        )
        self.bind("<Escape>", lambda _event: self.destroy())
        self._reload()
        self.grab_set()

    def _reload(self, select: int | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, printer in enumerate(self._printers):
            self.tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    printer.name,
                    printer.host,
                    "Yes" if printer.show_on_startup else "No",
                ),
            )
        if select is not None and 0 <= select < len(self._printers):
            self.tree.selection_set(str(select))
            self.tree.see(str(select))

    def _selected(self) -> int | None:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def _names_except(self, index: int | None) -> list[str]:
        return [p.name for i, p in enumerate(self._printers) if i != index]

    def _add(self) -> None:
        form = PrinterForm(self, None, self._names_except(None))
        self.wait_window(form)
        self.grab_set()
        if form.result is not None:
            self._printers.append(form.result)
            self._reload(select=len(self._printers) - 1)

    def _edit(self) -> None:
        index = self._selected()
        if index is None:
            return
        form = PrinterForm(self, self._printers[index], self._names_except(index))
        self.wait_window(form)
        self.grab_set()
        if form.result is not None:
            self._printers[index] = form.result
            self._reload(select=index)

    def _remove(self) -> None:
        index = self._selected()
        if index is None:
            return
        name = self._printers[index].name
        if messagebox.askyesno("Remove printer", f'Remove "{name}"?', parent=self):
            del self._printers[index]
            self._reload(select=min(index, len(self._printers) - 1))

    def _move(self, offset: int) -> None:
        index = self._selected()
        if index is None:
            return
        target = index + offset
        if 0 <= target < len(self._printers):
            printers = self._printers
            printers[index], printers[target] = printers[target], printers[index]
            self._reload(select=target)

    def _save(self) -> None:
        self._on_save(self._printers)
        self.destroy()


class App(ttk.Frame):
    """Main window."""

    def __init__(
        self, root: tk.Tk, settings: Settings, config_file: Path, warnings: list[str]
    ) -> None:
        super().__init__(root)
        self.root = root
        self.settings = settings
        self.config_file = config_file
        self.images = ImageCache()

        self._results: queue.Queue[tuple[int, str, PrinterStatus]] = queue.Queue()
        self._poll_slots = threading.Semaphore(MAX_CONCURRENT_POLLS)
        self._generation = 0
        self._in_flight: set[str] = set()
        self._refresh_job: str | None = None
        self._cards: dict[str, PrinterCard] = {}
        self._visible: dict[str, tk.BooleanVar] = {}

        root.title(f"{APP_NAME} v{__version__}")
        self._set_icons()
        self._configure_styles()
        self.background = ttk.Style().lookup("TFrame", "background") or root.cget("bg")
        root.configure(background=self.background)

        self._build_menu()
        self._build_side_panel()
        self.cards_row = ScrollableRow(self)
        self.cards_row.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.empty_state = ttk.Frame(self.cards_row.inner, padding=40)

        self.pack(fill=tk.BOTH, expand=True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<F5>", lambda _event: self.refresh_all())

        self.rebuild_printers(initial=True)
        self._drain_results()
        self._schedule_refresh()
        if warnings:
            root.after(200, lambda: messagebox.showwarning(APP_NAME, "\n\n".join(warnings)))

    # ----- window setup -------------------------------------------------

    def _set_icons(self) -> None:
        assets = paths.assets_dir()
        try:
            self._icon = tk.PhotoImage(file=str(assets / "icon.png"))
            self.root.iconphoto(True, self._icon)
            if sys.platform == "win32":
                self.root.iconbitmap(default=str(assets / "icon.ico"))
        except tk.TclError:
            log.exception("Could not load the window icon")

    def _configure_styles(self) -> None:
        style = ttk.Style()
        # The native Windows theme ignores progress bar colors. "clam" honors them.
        style.theme_use("clam")
        for color, hex_value in SUPPLY_BAR_COLORS.items():
            style.configure(
                supply_bar_style(color),
                background=hex_value,
                troughcolor="#e6e6e6",
                bordercolor="#9a9a9a",
                lightcolor=hex_value,
                darkcolor=hex_value,
            )
        style.configure(
            "paper.Horizontal.TProgressbar",
            background="#b0b0b0",
            troughcolor="#f2f2f2",
            bordercolor="#757575",
            lightcolor="#b0b0b0",
            darkcolor="#b0b0b0",
        )

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(
            label="Refresh now", accelerator="F5", command=self.refresh_all
        )
        file_menu.add_command(label="Edit printers...", command=self.edit_printers)
        file_menu.add_separator()
        file_menu.add_command(label="Open data folder", command=self.open_data_folder)
        file_menu.add_command(label="Open log file", command=self.open_log)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)
        menubar.add_cascade(label="File", menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.configure(menu=menubar)

    def _build_side_panel(self) -> None:
        panel = ttk.Frame(self, padding=10, borderwidth=2, relief=tk.RIDGE)
        panel.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)

        ttk.Label(panel, text="Printers", font=("TkDefaultFont", 10, "bold")).pack(
            anchor=tk.W
        )
        self.check_frame = ttk.Frame(panel)
        self.check_frame.pack(anchor=tk.W, fill=tk.X, pady=(4, 0))
        select_row = ttk.Frame(panel)
        select_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(
            select_row, text="All", width=7, command=lambda: self.set_all(True)
        ).pack(side=tk.LEFT)
        ttk.Button(
            select_row, text="None", width=7, command=lambda: self.set_all(False)
        ).pack(side=tk.RIGHT)

        # Everything below is anchored to the bottom of the panel.
        self.updated_text = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.updated_text, font=("TkDefaultFont", 8)).pack(
            side=tk.BOTTOM, anchor=tk.W, pady=(6, 0)
        )
        ttk.Button(panel, text="Refresh now", command=self.refresh_all).pack(
            side=tk.BOTTOM, fill=tk.X, pady=(6, 0)
        )
        refresh_row = ttk.Frame(panel)
        refresh_row.pack(side=tk.BOTTOM, anchor=tk.W, pady=(10, 0))
        self.auto_refresh = tk.BooleanVar(value=self.settings.auto_refresh)
        ttk.Checkbutton(
            refresh_row,
            text="Reload every",
            variable=self.auto_refresh,
            command=self._refresh_settings_changed,
        ).pack(side=tk.LEFT)
        self.refresh_seconds = tk.StringVar(value=str(self.settings.refresh_seconds))
        seconds = ttk.Spinbox(
            refresh_row,
            from_=5,
            to=86400,
            increment=30,
            width=6,
            textvariable=self.refresh_seconds,
            command=self._refresh_settings_changed,
        )
        seconds.pack(side=tk.LEFT, padx=4)
        seconds.bind("<Return>", lambda _event: self._refresh_settings_changed())
        seconds.bind("<FocusOut>", lambda _event: self._refresh_settings_changed())
        ttk.Label(refresh_row, text="seconds").pack(side=tk.LEFT)

        totals = ttk.Frame(panel)
        totals.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        self.deficit_text = tk.StringVar(value="0")
        self.reams_text = tk.StringVar(value="0.0")
        for row, (label, variable) in enumerate(
            (("Sheets needed:", self.deficit_text), ("Reams needed:", self.reams_text))
        ):
            ttk.Label(totals, text=label, font=("TkDefaultFont", 9, "italic")).grid(
                row=row, column=0, sticky=tk.W
            )
            ttk.Label(totals, textvariable=variable).grid(row=row, column=1, sticky=tk.E)
        totals.columnconfigure(1, weight=1)

    # ----- printer list -------------------------------------------------

    def _printer(self, name: str) -> PrinterConfig | None:
        return next((p for p in self.settings.printers if p.name == name), None)

    def rebuild_printers(self, initial: bool = False) -> None:
        """Recreate the checkboxes and cards after the printer list changes."""
        previously_shown = {name for name, var in self._visible.items() if var.get()}
        known_before = set(self._visible)
        self._generation += 1
        self._in_flight.clear()
        for card in self._cards.values():
            card.destroy()
        self._cards.clear()
        self._visible.clear()
        for child in self.check_frame.winfo_children():
            child.destroy()

        for printer in self.settings.printers:
            if initial or printer.name not in known_before:
                shown = printer.show_on_startup
            else:
                shown = printer.name in previously_shown
            variable = tk.BooleanVar(value=shown)
            self._visible[printer.name] = variable
            ttk.Checkbutton(
                self.check_frame,
                text=printer.name,
                variable=variable,
                command=lambda name=printer.name: self._toggle(name),
            ).pack(anchor=tk.W)
            if shown:
                self._cards[printer.name] = PrinterCard(self.cards_row.inner, printer, self)
        self._layout_cards()
        for name in list(self._cards):
            self.poll(name)
        self._update_totals()

    def _layout_cards(self) -> None:
        for child in self.empty_state.winfo_children():
            child.destroy()
        self.empty_state.pack_forget()
        for card in self._cards.values():
            card.pack_forget()
        for printer in self.settings.printers:
            card = self._cards.get(printer.name)
            if card is not None:
                card.pack(side=tk.LEFT, fill=tk.Y, anchor=tk.N)
        if self._cards:
            return
        if self.settings.printers:
            message = "No printers selected.\nTick a printer on the left to show it."
        else:
            message = "No printers have been added yet."
        ttk.Label(self.empty_state, text=message, justify=tk.CENTER).pack()
        if not self.settings.printers:
            ttk.Button(
                self.empty_state, text="Add printers...", command=self.edit_printers
            ).pack(pady=10)
        self.empty_state.pack()

    def _toggle(self, name: str) -> None:
        printer = self._printer(name)
        if printer is None:
            return
        if self._visible[name].get():
            if name not in self._cards:
                self._cards[name] = PrinterCard(self.cards_row.inner, printer, self)
                self.poll(name)
        else:
            card = self._cards.pop(name, None)
            if card is not None:
                card.destroy()
        self._layout_cards()
        self._update_totals()

    def set_all(self, shown: bool) -> None:
        for name, variable in self._visible.items():
            if variable.get() != shown:
                variable.set(shown)
                self._toggle(name)

    def edit_printers(self) -> None:
        PrinterEditor(self.root, self.settings.printers, self._apply_printers)

    def _apply_printers(self, printers: list[PrinterConfig]) -> None:
        self.settings.printers = printers
        self._save()
        self.rebuild_printers()

    def _save(self) -> None:
        try:
            save_settings(self.settings, self.config_file)
        except OSError as exc:
            log.exception("Could not save settings to %s", self.config_file)
            messagebox.showerror(
                APP_NAME, f"The settings could not be saved.\n\n{self.config_file}\n{exc}"
            )

    # ----- polling ------------------------------------------------------

    def poll(self, name: str) -> None:
        """Start a background poll of one printer unless one is running."""
        printer = self._printer(name)
        card = self._cards.get(name)
        if printer is None or card is None or name in self._in_flight:
            return
        self._in_flight.add(name)
        card.show_loading()
        generation = self._generation
        settings = copy.copy(self.settings)

        def work() -> None:
            with self._poll_slots:
                status = poll_printer(printer, settings)
            self._results.put((generation, name, status))

        threading.Thread(target=work, name=f"poll-{name}", daemon=True).start()

    def _drain_results(self) -> None:
        changed = False
        while True:
            try:
                generation, name, status = self._results.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation:
                continue  # The printer list changed while this poll was running.
            self._in_flight.discard(name)
            card = self._cards.get(name)
            if card is not None:
                card.show_status(status)
                changed = True
        if changed:
            self._update_totals()
            self.updated_text.set(f"Updated {datetime.now():%H:%M:%S}")
        self.root.after(QUEUE_POLL_MS, self._drain_results)

    def refresh_all(self) -> None:
        for name in list(self._cards):
            self.poll(name)

    def _update_totals(self) -> None:
        deficit = sum(
            card.status.paper_deficit
            for card in self._cards.values()
            if card.status is not None and card.status.ok
        )
        self.deficit_text.set(str(deficit))
        self.reams_text.set(f"{deficit / self.settings.ream_size:.1f}")

    def _refresh_settings_changed(self) -> None:
        text = self.refresh_seconds.get().strip()
        seconds = int(text) if text.isdigit() else self.settings.refresh_seconds
        seconds = max(5, min(86400, seconds))
        self.refresh_seconds.set(str(seconds))
        changed = (
            seconds != self.settings.refresh_seconds
            or self.auto_refresh.get() != self.settings.auto_refresh
        )
        if not changed:
            return
        self.settings.refresh_seconds = seconds
        self.settings.auto_refresh = self.auto_refresh.get()
        self._save()
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._refresh_job is not None:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None
        if self.settings.auto_refresh:
            self._refresh_job = self.root.after(
                self.settings.refresh_seconds * 1000, self._auto_refresh
            )

    def _auto_refresh(self) -> None:
        self._refresh_job = None
        self.refresh_all()
        self._schedule_refresh()

    # ----- menu actions -------------------------------------------------

    def open_data_folder(self) -> None:
        folder = paths.data_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            paths.open_path(folder)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open {folder}\n{exc}")

    def open_log(self) -> None:
        log_file = paths.log_path()
        if not log_file.exists():
            messagebox.showinfo(APP_NAME, "Nothing has been logged yet.")
            return
        try:
            paths.open_path(log_file)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open {log_file}\n{exc}")

    def show_about(self) -> None:
        messagebox.showinfo(
            "About",
            f"{APP_NAME} v{__version__}\n\n"
            "Shows toner and paper levels for network printers using SNMP "
            "and the standard Printer MIB.\n\n"
            "Released under the MIT License.",
        )

    def close(self) -> None:
        self.root.destroy()
