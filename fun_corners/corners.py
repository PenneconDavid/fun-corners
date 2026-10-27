"""Thread-safe hot-corner engine with observable, side-effect-free preview."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
from pathlib import PureWindowsPath
import threading
import time
from typing import Any

from . import actions
from . import config as cfg_mod
from .monitor import Corner, Monitor, find_corners, get_monitors, layout_key

_log = logging.getLogger(__name__)
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_user32.GetForegroundWindow.restype = wt.HWND
_user32.GetDesktopWindow.restype = wt.HWND
_user32.GetShellWindow.restype = wt.HWND
_user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
_user32.GetWindowRect.restype = wt.BOOL
_user32.IsZoomed.argtypes = [wt.HWND]
_user32.IsZoomed.restype = wt.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_user32.GetWindowThreadProcessId.restype = wt.DWORD
_kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
_kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.CloseHandle.restype = wt.BOOL


def _is_modifier_pressed(mod: str) -> bool:
    keys = {"ctrl": (0x11,), "shift": (0x10,), "alt": (0x12,), "win": (0x5B, 0x5C)}
    return mod == "none" or any(_user32.GetAsyncKeyState(vk) & 0x8000 for vk in keys.get(mod, ()))


def _is_fullscreen_active(monitors: list[Monitor], hwnd: int | None = None) -> bool:
    """Suppress true full-screen windows globally, but allow normal maximized apps."""
    hwnd = hwnd or _user32.GetForegroundWindow()
    if not hwnd or hwnd in (_user32.GetDesktopWindow(), _user32.GetShellWindow()):
        return False
    if _user32.IsZoomed(hwnd):
        return False
    rect = wt.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    return any(rect.left <= m.x and rect.top <= m.y and
               rect.right >= m.x + m.w and rect.bottom >= m.y + m.h for m in monitors)


def _foreground_executable(hwnd: int) -> str:
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    process = _kernel32.OpenProcess(0x1000, False, pid.value)
    if not process:
        return ""
    try:
        size = wt.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(process, 0, buf, ctypes.byref(size)):
            return PureWindowsPath(buf.value).name.casefold()
        return ""
    finally:
        _kernel32.CloseHandle(process)


class CornerEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._lock = threading.RLock()
        self.monitors: list[Monitor] = []
        self.corners: list[Corner] = []
        self.topology_version = 0
        self._layout_id = ""
        self._generation = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._enabled = bool(config.get("enabled", True))
        self._preview_mode = False
        self._pause_until = 0.0
        self._dwell_key: str | None = None
        self._dwell_start = 0.0
        self._blocked: tuple[int, int, float] | None = None
        self._status: dict[str, Any] = {}
        self._foreground_cache: tuple[int | None, float, str] = (None, 0.0, "")
        self.refresh_monitors()
        self._set_status("ready", "Ready")

    def _set_status(self, state: str, message: str, corner: Corner | None = None,
                    progress: float = 0.0) -> None:
        self._status = {"state": state, "message": message,
                        "corner_key": corner.key if corner else None,
                        "action": corner.action if corner else "disabled",
                        "progress": max(0.0, min(1.0, progress))}

    def report_error(self, message: str) -> None:
        with self._lock:
            self._set_status("error", message)

    def status_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def snapshot_layout(self) -> tuple[list[Monitor], list[Corner], int]:
        # Corner objects are never modified by the engine after publication.
        with self._lock:
            return list(self.monitors), list(self.corners), self.topology_version

    def _replace_layout(self, config: dict[str, Any], monitors: list[Monitor]) -> None:
        detected = find_corners(monitors, ignore_inner=config.get("ignore_inner_corners", True))
        current_id = layout_key(monitors)
        cfg_mod.migrate_corner_bindings(config, detected, current_id)
        for corner in detected:
            corner.action = cfg_mod.get_corner_action(config, corner.key, corner.binding_keys, layout_id=current_id)
        signature = lambda ms: [(m.name, m.x, m.y, m.w, m.h, m.primary, m.device_id) for m in ms]
        if signature(monitors) != signature(self.monitors) or [c.key for c in detected] != [c.key for c in self.corners]:
            self.topology_version += 1
        # Publish only after all geometry/config preparation has succeeded.
        if config is not self.config:
            self.config.clear()
            self.config.update(config)
        self.monitors, self.corners, self._layout_id = monitors, detected, current_id
        self._generation += 1
        self._reset_dwell()
        self._blocked = None

    def refresh_monitors(self) -> None:
        monitors = get_monitors()
        with self._lock:
            self._replace_layout(self.config, monitors)

    def apply_config(self, config: dict[str, Any]) -> None:
        with self._lock:
            candidate = cfg_mod.validate(config)
            # Apply uses the last successfully enumerated topology. Display changes
            # arrive separately, so a transient OS enumeration failure cannot turn
            # a successful disk save into a half-applied configuration.
            self._replace_layout(candidate, list(self.monitors))
            self._enabled = bool(self.config.get("enabled", True))
            self._foreground_cache = (None, 0.0, "")

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        with self._lock:
            self._generation += 1
            self._enabled = bool(value)
            self.config["enabled"] = self._enabled
            self._pause_until = 0.0
            self._reset_dwell()

    @property
    def preview_mode(self) -> bool:
        with self._lock:
            return self._preview_mode

    @preview_mode.setter
    def preview_mode(self, value: bool) -> None:
        with self._lock:
            self._generation += 1
            self._preview_mode = bool(value)
            self._reset_dwell()
            self._blocked = None

    def pause_for(self, minutes: int) -> None:
        if minutes not in (5, 15, 60):
            raise ValueError("Choose a 5, 15 or 60 minute pause")
        with self._lock:
            self._generation += 1
            self._pause_until = time.monotonic() + minutes * 60
            self._reset_dwell()

    def resume(self) -> None:
        self.enabled = True

    def _reset_dwell(self) -> None:
        self._dwell_key = None
        self._dwell_start = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="FunCorners-poll")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once(time.monotonic())
            except Exception as exc:
                _log.exception("Corner polling failed")
                self.report_error(f"Hot corner error: {exc}")
                with self._lock:
                    self._reset_dwell()
                self._stop.wait(0.5)
            self._stop.wait(0.02)

    def _poll_once(self, now: float, position: tuple[int, int] | None = None) -> None:
        with self._lock:
            dispatch = self._evaluate_locked(now, position)
        if dispatch is None:
            return
        hit, options, generation = dispatch
        with self._lock:
            if (generation != self._generation or self._preview_mode or
                    not self._enabled or self._pause_until or self._stop.is_set()):
                return
            # This is the dispatch reservation. Preview/pause cancels work that
            # has not reached this point; an action already started may finish.
        title = actions.ACTIONS.get(hit.action, hit.action)
        try:
            actions.execute(hit.action, options)
            state, message = "fired", f"Ran {title}"
        except Exception as exc:
            _log.exception("Action %s failed", hit.action)
            state, message = "error", f"Could not run {title}: {exc}"
        with self._lock:
            if generation == self._generation:
                self._set_status(state, message, hit, 1 if state == "fired" else 0)

    def _evaluate_locked(self, now: float, position: tuple[int, int] | None = None):
        if self._pause_until and now >= self._pause_until:
            self._pause_until = 0.0
            self._reset_dwell()
        if not self._enabled or self._pause_until:
            self._reset_dwell()
            text = "Paused" if not self._pause_until else f"Paused for {max(1, int((self._pause_until-now+59)//60))} more minutes"
            self._set_status("paused", text)
            return
        if not any(c.action != "disabled" for c in self.corners):
            self._reset_dwell()
            self._set_status("unassigned", "No corners assigned — choose an action in Settings")
            return
        if position is None:
            pt = wt.POINT()
            if not _user32.GetCursorPos(ctypes.byref(pt)):
                self._reset_dwell()
                self._set_status("unavailable", "Cursor unavailable")
                return
            position = pt.x, pt.y
        size = self.config.get("corner_size", 6)
        hwnd = _user32.GetForegroundWindow()
        if self.config.get("excluded_apps"):
            cached_hwnd, cached_time, executable = self._foreground_cache
            if hwnd != cached_hwnd or now - cached_time >= 1.0:
                executable = _foreground_executable(hwnd) if hwnd else ""
                self._foreground_cache = (hwnd, now, executable)
            if executable in self.config["excluded_apps"]:
                self._reset_dwell()
                self._set_status("excluded", f"Suppressed for {executable}")
                return
        if self.config.get("ignore_fullscreen") and _is_fullscreen_active(self.monitors, hwnd):
            self._reset_dwell()
            self._set_status("fullscreen", "Suppressed by a full-screen application")
            return
        hit = self._hit_test(*position, size)
        if self._blocked:
            bx, by, fired_at = self._blocked
            distance = max(abs(position[0]-bx), abs(position[1]-by))
            if distance > max(size*3, 25) and now-fired_at >= self.config.get("cooldown_ms", 1500)/1000:
                self._blocked = None
            else:
                self._reset_dwell()
                if self._status.get("state") in ("preview_fired", "fired", "error") and now-fired_at < 1.2:
                    return
                self._set_status("cooldown", "Move away from the corner to re-arm")
                return
        if hit is None or hit.action == "disabled":
            self._reset_dwell()
            self._set_status("preview" if self._preview_mode else "active",
                             "Preview — move to an assigned corner; no actions will run" if self._preview_mode else "Active")
            return
        options = cfg_mod.get_corner_options(self.config, hit, self._layout_id)
        modifier = options.get("modifier_key", self.config.get("modifier_key", "none"))
        if not _is_modifier_pressed(modifier):
            self._reset_dwell()
            self._set_status("modifier", f"Hold {modifier.title()} to activate {hit.label}", hit)
            return
        if hit.key != self._dwell_key:
            self._dwell_key, self._dwell_start = hit.key, now
        dwell = options.get("dwell_ms", self.config.get("dwell_ms", 300))/1000
        progress = min(1.0, (now-self._dwell_start)/dwell)
        title = actions.ACTIONS.get(hit.action, hit.action)
        if progress < 1:
            self._set_status("dwelling", f"{'Preview: ' if self._preview_mode else ''}{hit.label} — {title}", hit, progress)
            return
        # Record re-arm state even on errors; retry requires leaving the corner.
        self._blocked = (hit.x, hit.y, now)
        self._reset_dwell()
        if self._preview_mode:
            self._set_status("preview_fired", f"Would run {title} — preview only", hit, 1)
            return
        self._set_status("running", f"Running {title}", hit, 1)
        return hit, options, self._generation

    def _hit_test(self, cx: int, cy: int, size: int) -> Corner | None:
        for corner in self.corners:
            if (min(abs(cx-corner.x), abs(cx-(corner.x-1))) <= size and
                    min(abs(cy-corner.y), abs(cy-(corner.y-1))) <= size):
                return corner
        return None
