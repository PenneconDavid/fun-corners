"""Monitor detection and hot-corner identification.

Enumerates physical monitors via the Win32 ``EnumDisplayMonitors`` API, then
analyses the geometry of the combined virtual desktop to find every point
where the cursor would get *stuck* — i.e. the vertices of the boundary
polygon formed by the union of all monitor rectangles.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import json
from dataclasses import dataclass, field


# ── Win32 structures ─────────────────────────────────────────────────────────

class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize",    wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork",    wt.RECT),
        ("dwFlags",   wt.DWORD),
        ("szDevice",  wt.WCHAR * 32),
    ]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32),
        ("DeviceString", wt.WCHAR * 128), ("StateFlags", wt.DWORD),
        ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128),
    ]


_MONITORINFOF_PRIMARY = 0x00000001
_user32 = ctypes.windll.user32

_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int,                    # return (BOOL)
    ctypes.c_void_p,                 # hMonitor
    ctypes.c_void_p,                 # hdcMonitor
    ctypes.POINTER(wt.RECT),        # lprcMonitor
    ctypes.c_void_p,                 # dwData  (LPARAM)
)


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Monitor:
    """Describes a single physical monitor."""

    x: int
    y: int
    w: int
    h: int
    name: str
    primary: bool
    index: int = 0
    device_id: str = ""


@dataclass
class Corner:
    """A detected hot-corner on the virtual-desktop boundary."""

    x: int
    y: int
    label: str = ""
    action: str = "disabled"
    covered_quadrants: dict[str, bool] = field(default_factory=dict)
    is_inner: bool = False
    binding_keys: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Coordinate key for this layout; binding_keys follow the display."""
        return f"{self.x},{self.y}"


# ── Monitor enumeration ─────────────────────────────────────────────────────

_user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(_MONITORINFOEXW)]
_user32.GetMonitorInfoW.restype = wt.BOOL
_user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), _MONITORENUMPROC, ctypes.c_ssize_t]
_user32.EnumDisplayMonitors.restype = wt.BOOL
_user32.EnumDisplayDevicesW.argtypes = [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(_DISPLAY_DEVICEW), wt.DWORD]
_user32.EnumDisplayDevicesW.restype = wt.BOOL


def _display_identity(adapter_name: str) -> str:
    """Use the monitor device interface; adapter names are a best-effort fallback.

    Device interfaces normally survive desktop rearrangement and resolution
    changes. Reconnecting through a different GPU/port can change the interface.
    """
    device = _DISPLAY_DEVICEW()
    device.cb = ctypes.sizeof(device)
    if _user32.EnumDisplayDevicesW(adapter_name, 0, ctypes.byref(device), 1):
        if device.DeviceID:
            return device.DeviceID.casefold()
    return adapter_name.casefold()


def layout_key(monitors: list[Monitor]) -> str:
    """Stable signature for an exact connected-display topology."""
    topology = sorted(
        (m.device_id or m.name.casefold(), m.x, m.y, m.w, m.h, m.primary)
        for m in monitors
    )
    return hashlib.sha256(json.dumps(topology, separators=(",", ":")).encode()).hexdigest()[:24]


def get_monitors() -> list[Monitor]:
    """Return every connected monitor with its geometry."""
    monitors: list[Monitor] = []

    def _callback(hMonitor, _hdc, _lprc, _data):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not _user32.GetMonitorInfoW(hMonitor, ctypes.byref(info)):
            return 1
        r = info.rcMonitor
        monitors.append(Monitor(
            x=r.left, y=r.top,
            w=r.right - r.left,
            h=r.bottom - r.top,
            name=info.szDevice.rstrip("\x00"),
            primary=bool(info.dwFlags & _MONITORINFOF_PRIMARY),
            device_id=_display_identity(info.szDevice.rstrip("\x00")),
        ))
        return 1  # continue enumeration

    # prevent the Python callback wrapper from being garbage-collected
    cb = _MONITORENUMPROC(_callback)
    if not _user32.EnumDisplayMonitors(None, None, cb, 0):
        raise OSError("Windows could not enumerate the connected displays")

    # Sort: primary first, then left-to-right, top-to-bottom
    monitors.sort(key=lambda m: (not m.primary, m.x, m.y))
    for idx, m in enumerate(monitors):
        m.index = idx
    return monitors


# ── Corner detection ─────────────────────────────────────────────────────────

def _pixel_in_any_monitor(px: int, py: int, monitors: list[Monitor]) -> bool:
    """Return True if pixel (px, py) lies inside any monitor rectangle."""
    return any(
        m.x <= px < m.x + m.w and m.y <= py < m.y + m.h
        for m in monitors
    )


def _is_boundary_corner(quads: dict[str, bool]) -> bool:
    """Decide whether the quadrant-coverage pattern indicates a corner.

    Patterns:
    * 1 covered  → convex (outer) corner  ✓
    * 3 covered  → concave (inner/step) corner  ✓
    * 2 covered, **diagonal** (tl+br or tr+bl)  → saddle corner  ✓
    * 2 covered, **adjacent** (e.g. bl+br = top edge)  → edge midpoint  ✗
    * 0 or 4 covered  → outside or interior  ✗
    """
    n = sum(quads.values())
    if n in (0, 4):
        return False
    if n in (1, 3):
        return True
    # n == 2  →  corner only if the two covered quadrants are diagonal
    covered = {k for k, v in quads.items() if v}
    _adjacent_pairs = (
        {"tl", "tr"}, {"bl", "br"},   # horizontal edge
        {"tl", "bl"}, {"tr", "br"},   # vertical edge
    )
    return covered not in _adjacent_pairs


def find_corners(monitors: list[Monitor], ignore_inner: bool = False) -> list[Corner]:
    """Identify every hot-corner position on the virtual-desktop boundary.

    A hot corner is any vertex of a monitor rectangle where the cursor would
    get *trapped* because the desktop boundary changes direction (turns a
    right angle) at that point.
    """
    if not monitors:
        return []

    # Collect every unique vertex from every monitor
    candidates: set[tuple[int, int]] = set()
    for m in monitors:
        candidates |= {
            (m.x,       m.y),
            (m.x + m.w, m.y),
            (m.x,       m.y + m.h),
            (m.x + m.w, m.y + m.h),
        }

    corners: list[Corner] = []
    for px, py in candidates:
        quads = {
            "tl": _pixel_in_any_monitor(px - 1, py - 1, monitors),
            "tr": _pixel_in_any_monitor(px,     py - 1, monitors),
            "bl": _pixel_in_any_monitor(px - 1, py,     monitors),
            "br": _pixel_in_any_monitor(px,     py,     monitors),
        }
        if _is_boundary_corner(quads):
            is_inner = (sum(quads.values()) == 3)
            if ignore_inner and is_inner:
                continue
            corners.append(Corner(x=px, y=py, covered_quadrants=quads, is_inner=is_inner))

    _assign_labels(corners, monitors)
    # Stable ordering: top-to-bottom, then left-to-right
    corners.sort(key=lambda c: (c.y, c.x))
    return corners


# ── Label generation ─────────────────────────────────────────────────────────

def _assign_labels(corners: list[Corner], monitors: list[Monitor] | None = None) -> None:
    """Give each corner a human-readable label like 'Monitor 1: Top-Left' or 'Top-Left'."""
    if not corners:
        return

    for c in corners:
        # Determine which monitor this corner belongs to (or is adjacent to)
        corner_types = []
        if monitors:
            for idx, m in enumerate(monitors):
                # Check which corner of this monitor matches (c.x, c.y)
                is_tl = (c.x == m.x and c.y == m.y)
                is_tr = (c.x == m.x + m.w and c.y == m.y)
                is_bl = (c.x == m.x and c.y == m.y + m.h)
                is_br = (c.x == m.x + m.w and c.y == m.y + m.h)

                pos_str = ""
                if is_tl: pos_str = "Top-Left"
                elif is_tr: pos_str = "Top-Right"
                elif is_bl: pos_str = "Bottom-Left"
                elif is_br: pos_str = "Bottom-Right"

                if pos_str:
                    mon_label = f"M{idx+1} {pos_str}"
                    corner_types.append(mon_label)
                    identity = m.device_id or m.name.casefold()
                    c.binding_keys.append(f"{identity}|{pos_str.lower()}")

        if corner_types:
            c.label = " / ".join(corner_types)
        else:
            c.label = f"Corner ({c.x}, {c.y})"

        if c.is_inner:
            c.label += " (Inner Step)"
