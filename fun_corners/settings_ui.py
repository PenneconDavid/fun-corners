"""Resizable native settings, with a side-effect-free corner preview."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, TYPE_CHECKING

from . import actions
from . import config as cfg_mod
from .monitor import layout_key

if TYPE_CHECKING:
    from .corners import CornerEngine

_BG = "#f3f3f3"
_WHITE = "#ffffff"
_TEXT = "#202020"
_SUBTEXT = "#5c626b"
_ACCENT = "#0078d4"
_MON_FILL = "#e3ebf4"
_MON_BORDER = "#718296"
_DISABLED_C = "#8c949e"
_MODIFIERS = ("None", "Ctrl", "Shift", "Alt", "Win")


def _find_icon() -> Path | None:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    icon = base / "app_icon.ico"
    return icon if icon.exists() else None


def _number(value: str, label: str, low: int, high: int) -> int:
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label} must be a whole number.") from None
    if not low <= result <= high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    return result


def _excluded_apps(text: str) -> list[str]:
    names: list[str] = []
    for line in text.replace(",", "\n").splitlines():
        value = line.strip().strip('"')
        if not value:
            continue
        if Path(value).name != value or not value.lower().endswith(".exe") or any(c in value for c in '*?<>|'):
            raise ValueError("Excluded apps must be executable names, such as game.exe, one per line.")
        if value.lower() not in names:
            names.append(value.lower())
    return names


class SettingsWindow:
    """Settings may share the application's Tk event loop or run standalone."""

    def __init__(self, engine: "CornerEngine", on_save: Any = None,
                 on_ready: Any = None, master: tk.Misc | None = None,
                 block: bool = True) -> None:
        self.engine = engine
        self._on_save = on_save
        self._saving = False
        self._closed = False
        self._status_after: str | None = None
        self._selected: str | None = None
        self._dot_items: dict[str, tuple[float, float]] = {}
        self._loading_editor = False
        self._startup_warning = ""
        self._map_compact = False
        self._monitors, self._corners, self._version = engine.snapshot_layout()
        self._layout_id = layout_key(self._monitors)
        self._corner_edits = self._initial_edits()
        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        self.root.title("Fun Corners — Settings")
        self.root.configure(bg=_BG)
        self.root.resizable(True, True)
        icon = _find_icon()
        if icon:
            try:
                self.root.iconbitmap(str(icon))
            except tk.TclError:
                pass
        self._build_window()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda _event: self.close())
        self.root.bind("<Control-s>", self._save_key)
        self.root.bind("<Destroy>", self._on_destroy, add="+")
        self.root.after_idle(self._draw_layout)
        self._tick_status()
        if on_ready:
            on_ready(self)
        self.lift_to_top()
        if block:
            self.root.mainloop() if master is None else self.root.wait_window()

    def _initial_edits(self) -> dict[str, dict[str, str]]:
        edits = {}
        for corner in self._corners:
            options = cfg_mod.get_corner_options(self.engine.config, corner, self._layout_id)
            edits[corner.key] = {
                "action": cfg_mod.get_corner_action(self.engine.config, corner.key,
                    corner.binding_keys, self._layout_id),
                "dwell": str(options.get("dwell_ms", "")),
                "modifier": str(options.get("modifier_key", "inherit")).capitalize(),
                "shortcut": options.get("shortcut", ""),
                "app_path": options.get("app_path", ""),
            }
        return edits

    def _build_window(self) -> None:
        style = ttk.Style(self.root)
        style.configure("FC.TFrame", background=_BG)
        style.configure("FC.TLabel", background=_BG, foreground=_TEXT, font=("Segoe UI", 10))
        style.configure("FC.Header.TLabel", background=_BG, foreground=_TEXT, font=("Segoe UI", 17, "bold"))
        style.configure("FC.Sub.TLabel", background=_BG, foreground=_SUBTEXT, font=("Segoe UI", 9))
        style.configure("FC.Status.TLabel", background=_WHITE, foreground=_TEXT, font=("Segoe UI", 10))
        style.configure("FC.Save.TButton", font=("Segoe UI", 10, "bold"))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        heading = ttk.Frame(self.root, style="FC.TFrame", padding=(20, 16, 20, 8))
        heading.grid(row=0, column=0, sticky="ew")
        ttk.Label(heading, text="Fun Corners", style="FC.Header.TLabel").pack(anchor="w")
        ttk.Label(heading, text="Make the edges of your desktop work for you.", style="FC.Sub.TLabel").pack(anchor="w", pady=(3, 0))
        status = ttk.Frame(self.root, padding=(12, 9))
        status.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        status.columnconfigure(0, weight=1)
        self._status_var = tk.StringVar(value="Ready")
        self._status_label = ttk.Label(status, textvariable=self._status_var, wraplength=600)
        self._status_label.grid(row=0, column=0, sticky="w")
        self._progress = ttk.Progressbar(status, maximum=100)
        self._progress.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self._preview_var = tk.BooleanVar(value=self.engine.preview_mode)
        ttk.Checkbutton(status, text="Safe preview", variable=self._preview_var,
                        command=self._toggle_preview).grid(row=0, column=1, rowspan=2, padx=(16, 0))
        self._notebook = ttk.Notebook(self.root)
        self._notebook.grid(row=2, column=0, sticky="nsew", padx=20)
        corner_tab = ttk.Frame(self._notebook, padding=10)
        behavior_tab = ttk.Frame(self._notebook)
        self._notebook.add(corner_tab, text="Corners")
        self._notebook.add(behavior_tab, text="Behavior")
        self._build_corners(corner_tab)
        self._build_behavior(behavior_tab)
        self._notice_var = tk.StringVar(value="Preview uses saved settings. Save & apply before testing new choices.")
        self._notice = ttk.Label(self.root, textvariable=self._notice_var,
                                style="FC.Sub.TLabel", wraplength=700)
        self._notice.grid(row=3, column=0, sticky="ew", padx=20, pady=(8, 0))
        bar = ttk.Frame(self.root, style="FC.TFrame", padding=(20, 10, 20, 16))
        bar.grid(row=4, column=0, sticky="ew")
        ttk.Button(bar, text="Close", command=self.close).pack(side="right", padx=(8, 0))
        self._btn_save = ttk.Button(bar, text="Save & apply", command=self._on_save_click, style="FC.Save.TButton")
        self._btn_save.pack(side="right")
        ttk.Label(bar, text="Ctrl+S to save · Esc to close", style="FC.Sub.TLabel").pack(side="left")
        width = min(860, max(520, self.root.winfo_screenwidth() - 100))
        height = min(800, max(430, self.root.winfo_screenheight() - 100))
        self.root.minsize(min(560, width), min(440, height))
        self.root.geometry(f"{width}x{height}")
        self.root.bind("<Configure>", self._window_resized, add="+")
        warnings = getattr(cfg_mod, "load_warnings", [])
        if warnings:
            self._notice_var.set("Settings needed repair: " + "; ".join(warnings))
        elif self._startup_warning:
            self._notice_var.set(self._startup_warning)

    def _window_resized(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self._notice.configure(wraplength=max(250, event.width - 40))
            self._status_label.configure(wraplength=max(150, event.width - 210))
            self._layout_hint.configure(wraplength=max(250, event.width - 70))
            self._topology_notice.configure(wraplength=max(250, event.width - 70))
            compact = event.height < 650
            if compact != self._map_compact:
                self._map_compact = compact
                if compact:
                    self.canvas.grid_remove()
                else:
                    self.canvas.grid()
                self._set_layout_hint()

    def _set_layout_hint(self) -> None:
        prompt = ("Select a corner in the list below. Enlarge the window to see the monitor map."
                  if self._map_compact else "Select a numbered corner to edit it.")
        self._layout_hint_var.set(f"{len(self._monitors)} monitors · {len(self._corners)} usable corners. {prompt}")

    def _build_corners(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1, minsize=130)
        self._layout_hint_var = tk.StringVar()
        self._set_layout_hint()
        self._layout_hint = ttk.Label(parent, textvariable=self._layout_hint_var, wraplength=650)
        self._layout_hint.grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.canvas = tk.Canvas(parent, height=185, width=640, bg=_WHITE,
                                highlightthickness=1, highlightbackground="#c6cdd5")
        self.canvas.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<Configure>", lambda _event: self._draw_layout())
        self._topology_var = tk.StringVar()
        self._topology_notice = ttk.Label(parent, textvariable=self._topology_var, wraplength=650, foreground="#9a4b00")
        self._topology_notice.grid(row=2, column=0, sticky="ew", pady=5)
        editor = ttk.Panedwindow(parent, orient="horizontal")
        editor.grid(row=3, column=0, sticky="nsew")
        left = ttk.Frame(editor)
        right = ttk.Frame(editor, padding=(12, 0, 0, 0))
        editor.add(left, weight=2)
        editor.add(right, weight=3)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self._corner_list = tk.Listbox(left, exportselection=False, activestyle="none",
                                      font=("Segoe UI", 10), borderwidth=1, relief="solid",
                                      selectbackground=_ACCENT, selectforeground=_WHITE,
                                      height=6)
        self._corner_list.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(left, command=self._corner_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._corner_list.configure(yscrollcommand=scrollbar.set)
        self._corner_list.bind("<<ListboxSelect>>", self._list_selected)
        for i, corner in enumerate(self._corners):
            self._corner_list.insert("end", f"{i + 1}. {corner.label}")
        self._detail_canvas = tk.Canvas(right, highlightthickness=0)
        detail_scroll = ttk.Scrollbar(right, command=self._detail_canvas.yview)
        self._detail_canvas.configure(yscrollcommand=detail_scroll.set)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self._detail_canvas.grid(row=0, column=0, sticky="nsew")
        detail_scroll.grid(row=0, column=1, sticky="ns")
        details = ttk.Frame(self._detail_canvas, padding=(0, 0, 8, 10))
        item = self._detail_canvas.create_window(0, 0, window=details, anchor="nw")
        details.bind("<Configure>", lambda _event: self._detail_canvas.configure(scrollregion=self._detail_canvas.bbox("all")))
        self._detail_canvas.bind("<Configure>", lambda e: self._detail_canvas.itemconfigure(item, width=e.width))
        details.columnconfigure(0, weight=1)
        self._detail_title = tk.StringVar(value="Select a corner")
        ttk.Label(details, textvariable=self._detail_title, font=("Segoe UI", 11, "bold"), wraplength=320).grid(row=0, column=0, sticky="w", pady=(0, 9))
        self._action_var = tk.StringVar()
        self._dwell_override = tk.StringVar()
        self._modifier_override = tk.StringVar(value="Inherit")
        self._shortcut_var = tk.StringVar()
        self._app_path_var = tk.StringVar()
        ttk.Label(details, text="Action").grid(row=1, column=0, sticky="w")
        self._action_combo = ttk.Combobox(details, values=actions.ACTION_NAMES,
                                         textvariable=self._action_var, state="readonly")
        self._action_combo.grid(row=2, column=0, sticky="ew", pady=(3, 8))
        ttk.Label(details, text="Dwell delay in ms (blank = default)").grid(row=3, column=0, sticky="w")
        ttk.Entry(details, textvariable=self._dwell_override, width=12).grid(row=4, column=0, sticky="ew", pady=(3, 8))
        ttk.Label(details, text="Required key").grid(row=5, column=0, sticky="w")
        ttk.Combobox(details, values=("Inherit",) + _MODIFIERS, textvariable=self._modifier_override,
                     state="readonly").grid(row=6, column=0, sticky="ew", pady=(3, 8))
        self._shortcut_frame = ttk.Frame(details)
        self._shortcut_frame.grid(row=7, column=0, sticky="ew")
        self._shortcut_frame.columnconfigure(0, weight=1)
        ttk.Label(self._shortcut_frame, text="Shortcut (for example Ctrl+Alt+K)").grid(row=0, column=0, sticky="w")
        ttk.Entry(self._shortcut_frame, textvariable=self._shortcut_var).grid(row=1, column=0, sticky="ew", pady=(3, 8))
        self._app_frame = ttk.Frame(details)
        self._app_frame.grid(row=8, column=0, sticky="ew")
        self._app_frame.columnconfigure(0, weight=1)
        ttk.Label(self._app_frame, text="Application executable").grid(row=0, column=0, sticky="w")
        ttk.Entry(self._app_frame, textvariable=self._app_path_var).grid(row=1, column=0, sticky="ew", pady=(3, 8))
        ttk.Button(self._app_frame, text="Choose .exe…", command=self._choose_app).grid(row=2, column=0, sticky="w", pady=(0, 8))
        for var in (self._action_var, self._dwell_override, self._modifier_override,
                    self._shortcut_var, self._app_path_var):
            var.trace_add("write", self._editor_changed)
        if self._corners:
            self._select_corner(self._corners[0].key)
        else:
            self._action_combo.configure(state="disabled")

    def _build_behavior(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        canvas = tk.Canvas(parent, highlightthickness=0)
        scroll = ttk.Scrollbar(parent, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        body = ttk.Frame(canvas, padding=16)
        item = canvas.create_window(0, 0, window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        body.columnconfigure(1, weight=1)
        cfg = self.engine.config
        self._dwell_var = tk.StringVar(value=str(cfg.get("dwell_ms", 300)))
        self._size_var = tk.StringVar(value=str(cfg.get("corner_size", 6)))
        self._cooldown_var = tk.StringVar(value=str(cfg.get("cooldown_ms", 1500)))
        self._mod_var = tk.StringVar(value=cfg.get("modifier_key", "none").capitalize())
        for row, (label, variable, low, high, step) in enumerate((
            ("Default dwell delay (ms)", self._dwell_var, 50, 5000, 50),
            ("Corner size (px)", self._size_var, 1, 100, 1),
            ("Re-trigger cooldown (ms)", self._cooldown_var, 200, 10000, 250),
        )):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=(0, 10), padx=(0, 15))
            ttk.Spinbox(body, from_=low, to=high, increment=step, textvariable=variable,
                        width=10).grid(row=row, column=1, sticky="w", pady=(0, 10))
        ttk.Label(body, text="Default required key").grid(row=3, column=0, sticky="w", pady=(0, 12))
        ttk.Combobox(body, values=_MODIFIERS, textvariable=self._mod_var,
                     state="readonly", width=12).grid(row=3, column=1, sticky="w", pady=(0, 12))
        self._fullscreen_var = tk.BooleanVar(value=cfg.get("ignore_fullscreen", False))
        self._inner_var = tk.BooleanVar(value=cfg.get("ignore_inner_corners", True))
        try:
            auto_start = cfg_mod.get_auto_start()
        except OSError as exc:
            auto_start = cfg.get("auto_start", False)
            self._startup_warning = f"Could not read Windows startup settings: {exc}"
        self._autostart_var = tk.BooleanVar(value=auto_start)
        ttk.Checkbutton(body, text="Suspend for a fullscreen app on any monitor",
                        variable=self._fullscreen_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Label(body, text="Ordinary maximized windows stay enabled.", foreground=_SUBTEXT,
                  wraplength=500).grid(row=5, column=0, columnspan=2, sticky="w", padx=(22, 0), pady=(0, 7))
        ttk.Checkbutton(body, text="Ignore inner step corners between staggered monitors",
                        variable=self._inner_var).grid(row=6, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(body, text="Start automatically with Windows",
                        variable=self._autostart_var).grid(row=7, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Separator(body).grid(row=8, column=0, columnspan=2, sticky="ew", pady=15)
        ttk.Label(body, text="Excluded apps", font=("Segoe UI", 11, "bold")).grid(row=9, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text="Pause whenever one of these apps is in the foreground. One executable name per line, such as game.exe.",
                  wraplength=500).grid(row=10, column=0, columnspan=2, sticky="w", pady=(4, 7))
        self._exclusions = tk.Text(body, height=5, font=("Consolas", 10), wrap="none")
        self._exclusions.grid(row=11, column=0, columnspan=2, sticky="ew")
        self._exclusions.insert("1.0", "\n".join(cfg.get("excluded_apps", [])))
        ttk.Button(body, text="Add application…", command=self._add_exclusion).grid(row=12, column=0, sticky="w", pady=(7, 12))
        ttk.Label(body, text="Temporary pause", font=("Segoe UI", 11, "bold")).grid(row=13, column=0, columnspan=2, sticky="w", pady=(0, 7))
        pauses = ttk.Frame(body)
        pauses.grid(row=14, column=0, columnspan=2, sticky="w")
        for minutes in (5, 15, 60):
            ttk.Button(pauses, text=f"{minutes} min", command=lambda m=minutes: self.engine.pause_for(m)).pack(side="left", padx=(0, 6))
        ttk.Button(pauses, text="Resume now", command=self.engine.resume).pack(side="left")
        self._scroll_canvases = (canvas, self._detail_canvas)
        self.root.bind("<MouseWheel>", self._scroll_wheel, add="+")

    def _scroll_wheel(self, event: tk.Event) -> None:
        # Keep dropdown and corner-list wheel behavior local to those controls.
        target = event.widget
        if isinstance(target, (ttk.Combobox, tk.Listbox)):
            return
        for canvas in self._scroll_canvases:
            ancestor = target
            while ancestor is not None:
                if ancestor is canvas:
                    canvas.yview_scroll(-int(event.delta / 120), "units")
                    return
                ancestor = getattr(ancestor, "master", None)

    def _editor_changed(self, *_args: Any) -> None:
        if self._loading_editor or self._selected is None:
            return
        edit = self._corner_edits[self._selected]
        edit.update(action={v: k for k, v in actions.ACTIONS.items()}.get(self._action_var.get(), "disabled"),
                    dwell=self._dwell_override.get(), modifier=self._modifier_override.get(),
                    shortcut=self._shortcut_var.get(), app_path=self._app_path_var.get())
        self._update_action_fields()
        self._draw_layout()

    def _update_action_fields(self) -> None:
        action = self._corner_edits.get(self._selected, {}).get("action")
        self._shortcut_frame.grid() if action == "custom_shortcut" else self._shortcut_frame.grid_remove()
        self._app_frame.grid() if action == "launch_app" else self._app_frame.grid_remove()

    def _select_corner(self, key: str) -> None:
        self._selected = key
        index = next(i for i, c in enumerate(self._corners) if c.key == key)
        corner = self._corners[index]
        edit = self._corner_edits[key]
        self._loading_editor = True
        try:
            self._detail_title.set(f"{index + 1}. {corner.label}")
            self._action_var.set(actions.ACTIONS.get(edit["action"], actions.ACTIONS["disabled"]))
            self._dwell_override.set(edit["dwell"])
            self._modifier_override.set(edit["modifier"])
            self._shortcut_var.set(edit["shortcut"])
            self._app_path_var.set(edit["app_path"])
        finally:
            self._loading_editor = False
        self._corner_list.selection_clear(0, "end")
        self._corner_list.selection_set(index)
        self._corner_list.see(index)
        self._update_action_fields()
        self._draw_layout()

    def _list_selected(self, _event: Any = None) -> None:
        selection = self._corner_list.curselection()
        if selection:
            self._select_corner(self._corners[selection[0]].key)

    def _draw_layout(self) -> None:
        self.canvas.delete("all")
        width = max(300, self.canvas.winfo_width())
        height = max(140, self.canvas.winfo_height())
        if not self._monitors:
            self.canvas.create_text(width / 2, height / 2, text="No monitors detected", fill=_SUBTEXT)
            return
        min_x, min_y = min(m.x for m in self._monitors), min(m.y for m in self._monitors)
        total_w = max(m.x + m.w for m in self._monitors) - min_x
        total_h = max(m.y + m.h for m in self._monitors) - min_y
        scale = min((width - 60) / max(1, total_w), (height - 46) / max(1, total_h))
        ox, oy = (width - total_w * scale) / 2, (height - total_h * scale) / 2
        def point(x: int, y: int) -> tuple[float, float]:
            return ox + (x - min_x) * scale, oy + (y - min_y) * scale
        for i, monitor in enumerate(self._monitors):
            x1, y1 = point(monitor.x, monitor.y)
            x2, y2 = point(monitor.x + monitor.w, monitor.y + monitor.h)
            self.canvas.create_rectangle(x1, y1, x2, y2, fill=_MON_FILL, outline=_MON_BORDER, width=2)
            self.canvas.create_text((x1+x2)/2, (y1+y2)/2 - 7, text=f"Monitor {i+1}" + (" · Primary" if monitor.primary else ""), font=("Segoe UI", 10), fill=_TEXT)
            self.canvas.create_text((x1+x2)/2, (y1+y2)/2 + 10, text=f"{monitor.w} × {monitor.h}", font=("Segoe UI", 8), fill=_SUBTEXT)
        self._dot_items = {}
        for i, corner in enumerate(self._corners):
            x, y = point(corner.x, corner.y)
            color = _ACCENT if self._corner_edits[corner.key]["action"] != "disabled" else _DISABLED_C
            selected = corner.key == self._selected
            if selected:
                self.canvas.create_oval(x-15, y-15, x+15, y+15, outline=_ACCENT, width=2)
            tag = f"corner_{i}"
            self.canvas.create_oval(x-11, y-11, x+11, y+11, fill=color, outline=_WHITE, width=2, tags=tag)
            self.canvas.create_text(x, y, text=str(i+1), fill=_WHITE, font=("Segoe UI", 8, "bold"), tags=tag)
            self.canvas.tag_bind(tag, "<Button-1>", lambda _e, key=corner.key: self._select_corner(key))
            self._dot_items[corner.key] = (x, y)
        self._draw_live_corner()

    def _draw_live_corner(self, snapshot: dict[str, Any] | None = None) -> None:
        self.canvas.delete("live_corner")
        status = snapshot if snapshot is not None else self.engine.status_snapshot()
        key = status.get("corner_key")
        if key in self._dot_items:
            x, y = self._dot_items[key]
            self.canvas.create_oval(x-19, y-19, x+19, y+19, outline=_ACCENT, width=2, dash=(3, 2), tags="live_corner")
            progress = max(0.0, min(1.0, float(status.get("progress", 0))))
            if progress:
                self.canvas.create_arc(x-20, y-20, x+20, y+20, start=90, extent=-359*progress,
                                       style="arc", outline=_ACCENT, width=3, tags="live_corner")

    def _tick_status(self) -> None:
        if self._closed:
            return
        status = self.engine.status_snapshot()
        self._status_var.set(status.get("message", status.get("state", "Ready")))
        self._progress["value"] = max(0.0, min(1.0, float(status.get("progress", 0)))) * 100
        self._draw_live_corner(status)
        if self.engine.topology_version != self._version:
            self._topology_var.set("Monitor layout changed. Save your edits, then reopen Settings to edit the new layout.")
        self._status_after = self.root.after(100, self._tick_status)

    def _toggle_preview(self) -> None:
        self.engine.preview_mode = self._preview_var.get()
        self._notice_var.set("Preview uses saved settings. Save & apply before testing new choices. "
                             + ("Actions are currently blocked." if self._preview_var.get() else "Live actions are enabled."))

    def _choose_app(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="Choose application",
                                         filetypes=[("Windows applications", "*.exe")])
        if path:
            self._app_path_var.set(path)

    def _add_exclusion(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="Choose an app to exclude",
                                         filetypes=[("Windows applications", "*.exe")])
        if path:
            current = self._exclusions.get("1.0", "end-1c").rstrip()
            self._exclusions.delete("1.0", "end")
            self._exclusions.insert("1.0", current + ("\n" if current else "") + Path(path).name)

    def _prepare_config(self) -> dict[str, Any]:
        candidate = deepcopy(self.engine.config)
        candidate["dwell_ms"] = _number(self._dwell_var.get(), "Dwell delay", 50, 5000)
        candidate["corner_size"] = _number(self._size_var.get(), "Corner size", 1, 100)
        candidate["cooldown_ms"] = _number(self._cooldown_var.get(), "Cooldown", 200, 10000)
        candidate["modifier_key"] = self._mod_var.get().lower()
        candidate["ignore_fullscreen"] = self._fullscreen_var.get()
        candidate["ignore_inner_corners"] = self._inner_var.get()
        candidate["auto_start"] = self._autostart_var.get()
        candidate["excluded_apps"] = _excluded_apps(self._exclusions.get("1.0", "end-1c"))
        for corner in self._corners:
            edit = self._corner_edits[corner.key]
            action = edit["action"]
            options: dict[str, Any] = {}
            if edit["dwell"].strip():
                options["dwell_ms"] = _number(edit["dwell"], f"{corner.label}: dwell delay", 50, 5000)
            modifier = edit["modifier"].lower()
            if modifier != "inherit":
                options["modifier_key"] = modifier
            if action == "custom_shortcut":
                shortcut = edit["shortcut"].strip()
                error = actions.validate_shortcut(shortcut)
                if error:
                    raise ValueError(f"{corner.label}: {error}")
                options["shortcut"] = shortcut
            elif action == "launch_app":
                path = Path(edit["app_path"].strip().strip('"'))
                if path.suffix.lower() != ".exe" or not path.is_file():
                    raise ValueError(f"{corner.label}: choose an existing .exe application.")
                options["app_path"] = str(path.resolve())
            cfg_mod.set_corner_action(candidate, corner.key, action, corner.binding_keys, self._layout_id)
            cfg_mod.set_corner_options(candidate, corner, options, self._layout_id)
        return candidate

    def _on_save_click(self) -> None:
        if self._saving:
            return
        self._saving = True
        self._btn_save.configure(state="disabled", text="Saving…")
        previous_start: bool | None = None
        changed_start = False
        try:
            candidate = self._prepare_config()
            previous_start = cfg_mod.get_auto_start()
            changed_start = candidate["auto_start"] != previous_start
            if changed_start:
                cfg_mod.set_auto_start(candidate["auto_start"])
            try:
                cfg_mod.save(candidate)
            except Exception:
                if changed_start:
                    cfg_mod.set_auto_start(previous_start)
                raise
            self.engine.apply_config(candidate)
            if self._on_save:
                self._on_save()
            self._notice_var.set("Settings saved. " + ("Safe preview is on; actions are blocked." if self.engine.preview_mode else "Changes are active."))
        except Exception as exc:
            self._notice_var.set(f"Settings were not applied: {exc}")
            messagebox.showerror("Could not save settings", str(exc), parent=self.root)
        finally:
            self._saving = False
            if not self._closed:
                self._btn_save.configure(state="normal", text="Save & apply")

    def _save_key(self, _event: Any = None) -> str:
        self._on_save_click()
        return "break"

    def lift_to_top(self) -> None:
        if self._closed:
            return
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self._finish_close()

    def _finish_close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.engine.preview_mode = False
        if self._status_after is not None:
            try:
                self.root.after_cancel(self._status_after)
            except tk.TclError:
                pass
            self._status_after = None

    def close(self) -> None:
        if self._closed:
            return
        self._finish_close()
        self.root.destroy()

    def _on_cancel(self) -> None:
        self.close()
