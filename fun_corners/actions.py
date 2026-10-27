"""Windows actions and validated keyboard/application shortcuts."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from pathlib import Path
import subprocess
from typing import Any, Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER, SC_SCREENSAVE = 0xF170, 0xF140
MONITOR_OFF, SMTO_ABORTIFHUNG = 2, 0x0002
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
VK_SHIFT, VK_LWIN, VK_TAB = 0x10, 0x5B, 0x09
VK_D, VK_A, VK_S, VK_E = 0x44, 0x41, 0x53, 0x45
VK_VOLUME_MUTE, VK_MEDIA_PLAY_PAUSE = 0xAD, 0xB3
_MODIFIERS = (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)
_EXTENDED = {0xA3, 0xA5, 0x5B, 0x5C, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]

class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wt.DWORD), ("data", _INPUTUNION)]

user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wt.SHORT
user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
                                      wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
user32.LockWorkStation.argtypes = []
user32.LockWorkStation.restype = wt.BOOL

ACTIONS: dict[str, str] = {
    "disabled": "Disabled (No Action)", "sleep_displays": "Sleep Displays",
    "screensaver": "Start Screensaver", "show_desktop": "Show Desktop (Win+D)",
    "task_view": "Task View (Win+Tab)", "snipping_tool": "Screen Snip (Win+Shift+S)",
    "action_center": "Action Center / Quick Settings (Win+A)", "windows_search": "Windows Search (Win+S)",
    "file_explorer": "File Explorer (Win+E)", "start_menu": "Start Menu (Win)",
    "mute_volume": "Mute / Unmute Volume", "play_pause": "Play / Pause Media",
    "lock": "Lock Workstation", "custom_shortcut": "Custom Keyboard Shortcut",
    "launch_app": "Launch Application",
}
ACTION_IDS = list(ACTIONS)
ACTION_NAMES = list(ACTIONS.values())


def _send_events(events: list[tuple[int, bool]]) -> int:
    inputs = (INPUT * len(events))()
    for item, (vk, up) in zip(inputs, events):
        item.type = 1  # INPUT_KEYBOARD
        item.ki = KEYBDINPUT(vk, 0, (KEYEVENTF_KEYUP if up else 0) |
                            (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0), 0, 0)
    return int(user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT)))


def _press_keys(*vks: int) -> None:
    """Send one atomic batch, neutralizing and restoring existing modifiers.

    SendInput does not clear held keys. Releasing modifiers around the chord
    prevents Ctrl + Win+D becoming the Create Desktop shortcut. Both sides of
    each modifier are tracked, including modifiers used by the chord itself.
    """
    held = [vk for vk in _MODIFIERS if user32.GetAsyncKeyState(vk) & 0x8000]
    keys = [0xA0 if vk == VK_SHIFT else vk for vk in vks]
    events = ([(vk, True) for vk in held] + [(vk, False) for vk in keys] +
              [(vk, True) for vk in reversed(keys)] + [(vk, False) for vk in held])
    sent = 0
    try:
        sent = _send_events(events)
        if sent != len(events):
            raise OSError(ctypes.get_last_error(), "Windows blocked some keyboard input; try a non-elevated app.")
    except Exception:
        # Reconcile the accepted prefix rather than blindly releasing a key
        # the user was already holding. A cleanup failure is reported upstream.
        down = set(held)
        for vk, up in events[:sent]:
            down.discard(vk) if up else down.add(vk)
        cleanup = [(vk, True) for vk in reversed(keys) if vk in down and vk not in held]
        cleanup += [(vk, False) for vk in held if vk not in down]
        if cleanup:
            _send_events(cleanup)
        raise


def _send_syscommand_safe(wparam: int, lparam: int) -> None:
    result = ctypes.c_size_t()
    if not user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SYSCOMMAND, wparam, lparam,
                                     SMTO_ABORTIFHUNG, 1000, ctypes.byref(result)):
        raise OSError(ctypes.get_last_error(), "Windows could not complete the display action.")


def sleep_displays() -> None: _send_syscommand_safe(SC_MONITORPOWER, MONITOR_OFF)
def start_screensaver() -> None: _send_syscommand_safe(SC_SCREENSAVE, 0)
def show_desktop() -> None: _press_keys(VK_LWIN, VK_D)
def task_view() -> None: _press_keys(VK_LWIN, VK_TAB)
def snipping_tool() -> None: _press_keys(VK_LWIN, VK_SHIFT, VK_S)
def action_center() -> None: _press_keys(VK_LWIN, VK_A)
def windows_search() -> None: _press_keys(VK_LWIN, VK_S)
def file_explorer() -> None: _press_keys(VK_LWIN, VK_E)
def start_menu() -> None: _press_keys(VK_LWIN)
def mute_volume() -> None: _press_keys(VK_VOLUME_MUTE)
def play_pause() -> None: _press_keys(VK_MEDIA_PLAY_PAUSE)

def lock_workstation() -> None:
    if not user32.LockWorkStation():
        raise OSError(ctypes.get_last_error(), "Windows could not lock this session.")


_SHORTCUT_MODIFIERS = {"ctrl": 0xA2, "control": 0xA2, "shift": 0xA0, "alt": 0xA4, "win": 0x5B}
_SHORTCUT_KEYS = {"tab": 0x09, "enter": 0x0D, "escape": 0x1B, "esc": 0x1B,
                  "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
                  "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
                  "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}


def parse_shortcut(value: str) -> tuple[int, ...]:
    if not isinstance(value, str):
        raise ValueError("Enter a shortcut such as Ctrl+Alt+K.")
    parts = [p.strip().lower() for p in value.split("+")]
    if not parts or not all(parts):
        raise ValueError("Enter a shortcut such as Ctrl+Alt+K.")
    modifiers = []
    for part in parts[:-1]:
        if part not in _SHORTCUT_MODIFIERS or _SHORTCUT_MODIFIERS[part] in modifiers:
            raise ValueError("Use Ctrl, Shift, Alt, or Win once, followed by one key.")
        modifiers.append(_SHORTCUT_MODIFIERS[part])
    key = parts[-1]
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key in _SHORTCUT_KEYS:
        vk = _SHORTCUT_KEYS[key]
    else:
        raise ValueError("Use a letter, digit, F1–F24, or a named navigation key.")
    return (*modifiers, vk)


def validate_shortcut(value: str) -> str | None:
    try:
        parse_shortcut(value)
    except ValueError as exc:
        return str(exc)
    return None


def _launch_app(path: str) -> None:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Choose an application executable.")
    exe = Path(path)
    if not exe.is_absolute() or exe.suffix.lower() != ".exe" or not exe.is_file():
        raise ValueError("Choose an existing application .exe using its full path.")
    subprocess.Popen([str(exe)], cwd=str(exe.parent), shell=False)


_DISPATCH: dict[str, Callable[[], None]] = {
    "sleep_displays": sleep_displays, "screensaver": start_screensaver,
    "show_desktop": show_desktop, "task_view": task_view, "snipping_tool": snipping_tool,
    "action_center": action_center, "windows_search": windows_search, "file_explorer": file_explorer,
    "start_menu": start_menu, "mute_volume": mute_volume, "play_pause": play_pause, "lock": lock_workstation,
}

def execute(action_id: str, options: dict[str, Any] | None = None) -> None:
    options = options or {}
    if action_id == "custom_shortcut":
        _press_keys(*parse_shortcut(options.get("shortcut", "")))
    elif action_id == "launch_app":
        _launch_app(options.get("app_path", ""))
    elif action_id in _DISPATCH:
        _DISPATCH[action_id]()
