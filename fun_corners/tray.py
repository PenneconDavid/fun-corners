"""System-tray icon for Fun Corners (via *pystray*)."""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

import pystray
from PIL import Image, ImageDraw

from . import config as cfg_mod

if TYPE_CHECKING:
    from .corners import CornerEngine


# ── Icon rendering ───────────────────────────────────────────────────────────

def _create_icon_image(size: int = 64, enabled: bool = True) -> Image.Image:
    """Draw a small tray icon: a rounded square with four corner dots."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = 2
    bg = (45, 45, 48) if enabled else (80, 80, 80)
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=size // 6,
        fill=bg,
        outline=(120, 120, 130),
        width=2,
    )

    accent = (0, 150, 255) if enabled else (120, 120, 120)
    inset = size // 4
    r = size // 10
    for cx, cy in (
        (inset, inset),
        (size - inset, inset),
        (inset, size - inset),
        (size - inset, size - inset),
    ):
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent)

    return img


# ── Tray creation ────────────────────────────────────────────────────────────

def update_status(icon: pystray.Icon, active: bool) -> None:
    icon.icon = _create_icon_image(enabled=active)

def create_tray(
    engine: "CornerEngine",
    config: dict[str, Any],
    on_settings: Callable[[], None],
    on_quit: Callable[[], None],
    on_command: Callable[[str], None] | None = None,
) -> pystray.Icon:
    """Build and return a ``pystray.Icon`` (the caller must call ``.run()``)."""

    def _toggle(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        if on_command:
            on_command("toggle")
            return
        engine.enabled = not engine.enabled
        cfg_mod.save(config)
        icon.icon = _create_icon_image(enabled=engine.enabled)
        icon.title = f"Fun Corners {'(active)' if engine.enabled else '(paused)'}"

    def _settings(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        on_settings()

    def _quit(icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        on_quit()

    def _control(command: str) -> Callable:
        def callback(_icon, _item):
            if on_command:
                on_command(command)
            elif command == "resume":
                engine.resume()
            else:
                engine.pause_for(int(command.split(":")[1]))
        return callback

    menu = pystray.Menu(
        pystray.MenuItem(
            "Enabled",
            _toggle,
            checked=lambda _item: engine.enabled,
        ),
        pystray.MenuItem("Pause for", pystray.Menu(*(
            pystray.MenuItem(f"{minutes} minutes", _control(f"pause:{minutes}"))
            for minutes in (5, 15, 60)
        ))),
        pystray.MenuItem("Resume now", _control("resume")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Settings\u2026", _settings, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Fun Corners", _quit),
    )

    return pystray.Icon(
        name="FunCorners",
        icon=_create_icon_image(enabled=engine.enabled),
        title=f"Fun Corners ({'active' if engine.enabled else 'paused'})",
        menu=menu,
    )
