"""Validated, atomic settings storage and Windows startup integration."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from typing import Any

DEFAULTS: dict[str, Any] = {
    "corners": {}, "bindings": {}, "corner_options": {}, "layouts": {}, "layout_options": {},
    "dwell_ms": 300, "corner_size": 6, "cooldown_ms": 1500,
    "auto_start": False, "enabled": True, "modifier_key": "none",
    "ignore_fullscreen": False, "ignore_inner_corners": True,
    "excluded_apps": [],
}
_MODIFIERS = {"none", "ctrl", "shift", "alt", "win"}
_NUMERIC_BOUNDS = {"dwell_ms": (50, 5000), "corner_size": (1, 100), "cooldown_ms": (200, 10000)}
_save_lock = threading.RLock()
load_warnings: list[str] = []
_invalid_source: tuple[Path, bytes] | None = None


def _config_dir() -> Path:
    directory = Path(os.environ.get("APPDATA", Path.home())) / "FunCorners"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _config_path() -> Path:
    return _config_dir() / "config.json"


def _action_ids() -> set[str]:
    from .actions import ACTIONS
    return set(ACTIONS)


def _action_map(value: Any, name: str, warnings: list[str]) -> dict[str, str]:
    if not isinstance(value, dict):
        warnings.append(f"{name} must be an object; ignored its invalid value.")
        return {}
    allowed = _action_ids()
    valid = {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str) and v in allowed}
    if len(valid) != len(value):
        warnings.append(f"{name} contained unknown actions; those entries were ignored.")
    return valid


def _validated_options(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    options: dict[str, Any] = {}
    if type(value.get("dwell_ms")) is int and 50 <= value["dwell_ms"] <= 5000:
        options["dwell_ms"] = value["dwell_ms"]
    if isinstance(value.get("modifier_key"), str) and value["modifier_key"] in _MODIFIERS:
        options["modifier_key"] = value["modifier_key"]
    if isinstance(value.get("shortcut"), str):
        from .actions import validate_shortcut
        shortcut = value["shortcut"].strip()
        if validate_shortcut(shortcut) is None:
            options["shortcut"] = shortcut
    if isinstance(value.get("app_path"), str):
        options["app_path"] = value["app_path"].strip()
    return options


def validate(cfg: Any, warnings: list[str] | None = None) -> dict[str, Any]:
    """Repair untrusted JSON values without changing the supplied dictionary."""
    warnings = warnings if warnings is not None else []
    result = deepcopy(DEFAULTS)
    if not isinstance(cfg, dict):
        warnings.append("The settings file must contain a JSON object; defaults were loaded.")
        return result
    # Retain unknown fields for forward compatibility, while validating known fields.
    result.update(deepcopy(cfg))
    for key, bounds in _NUMERIC_BOUNDS.items():
        value = cfg.get(key, DEFAULTS[key])
        if type(value) is not int or not bounds[0] <= value <= bounds[1]:
            warnings.append(f"{key} must be an integer from {bounds[0]} to {bounds[1]}; its default was loaded.")
            value = DEFAULTS[key]
        result[key] = value
    for key in ("auto_start", "enabled", "ignore_fullscreen", "ignore_inner_corners"):
        value = cfg.get(key, DEFAULTS[key])
        if type(value) is not bool:
            warnings.append(f"{key} must be true or false; its default was loaded.")
            value = DEFAULTS[key]
        result[key] = value
    modifier = cfg.get("modifier_key", "none")
    if not isinstance(modifier, str) or modifier not in _MODIFIERS:
        warnings.append("Unknown modifier key; no modifier is required.")
        modifier = "none"
    result["modifier_key"] = modifier
    for key in ("corners", "bindings"):
        result[key] = _action_map(cfg.get(key, {}), key, warnings)
    layouts = cfg.get("layouts", {})
    result["layouts"] = {}
    if isinstance(layouts, dict):
        for layout_id, assignments in layouts.items():
            if isinstance(layout_id, str):
                result["layouts"][layout_id] = _action_map(assignments, "layout", warnings)
    else:
        warnings.append("layouts must be an object; saved layouts were ignored.")
    options = cfg.get("corner_options", {})
    result["corner_options"] = {}
    if isinstance(options, dict):
        for binding, values in options.items():
            if isinstance(binding, str):
                repaired = _validated_options(values)
                result["corner_options"][binding] = repaired
                if not isinstance(values, dict) or repaired != values:
                    warnings.append("Invalid per-corner options were ignored.")
    else:
        warnings.append("corner_options must be an object; its invalid value was ignored.")
    layout_options = cfg.get("layout_options", {})
    result["layout_options"] = {}
    if isinstance(layout_options, dict):
        for profile_id, entries in layout_options.items():
            if isinstance(profile_id, str) and isinstance(entries, dict):
                result["layout_options"][profile_id] = {}
                for coordinate, values in entries.items():
                    if isinstance(coordinate, str):
                        repaired = _validated_options(values)
                        result["layout_options"][profile_id][coordinate] = repaired
                        if not isinstance(values, dict) or repaired != values:
                            warnings.append("Invalid saved-layout corner options were ignored.")
            else:
                warnings.append("Invalid saved-layout options profile was ignored.")
    else:
        warnings.append("layout_options must be an object; its invalid value was ignored.")
    excluded = cfg.get("excluded_apps", [])
    if not isinstance(excluded, list) or any(not isinstance(v, str) for v in excluded):
        warnings.append("excluded_apps must be a list of executable names; exclusions were ignored.")
        excluded = []
    result["excluded_apps"] = sorted({v.strip().casefold() for v in excluded if v.strip()})
    return result


def load() -> dict[str, Any]:
    """Load defaults plus validated settings; leave the original file untouched."""
    global _invalid_source
    load_warnings.clear()
    _invalid_source = None
    raw: bytes | None = None
    path: Path | None = None
    try:
        path = _config_path()
        if not path.exists():
            return deepcopy(DEFAULTS)
        raw = path.read_bytes()
        saved = json.loads(raw.decode("utf-8-sig"))
        cfg = validate(saved, load_warnings)
    except (ValueError, UnicodeError, OSError) as exc:
        load_warnings.append(f"Could not read settings ({exc}); defaults were loaded.")
        cfg = deepcopy(DEFAULTS)
    if load_warnings and raw is not None and path is not None:
        _invalid_source = (path, raw)
    return cfg


def save(cfg: dict[str, Any]) -> None:
    """Atomically replace settings, preserving an invalid source before repair.

    Failed writes leave the original settings in place and propagate to the UI.
    Unique temporary names avoid competing tray/settings writers sharing a file.
    """
    global _invalid_source
    with _save_lock:
        path = _config_path()
        normalized = validate(cfg)
        if _invalid_source is not None and _invalid_source[0] == path:
            original = _invalid_source[1]
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = path.with_name(f"config.before-repair-{stamp}.json")
            with backup.open("xb") as stream:
                stream.write(original)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix="config-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump(normalized, stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            _invalid_source = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


def get_corner_action(cfg: dict[str, Any], corner_key: str, binding_keys: list[str] | None = None,
                      layout_id: str | None = None) -> str:
    """Prefer an exact saved topology, then the physical monitor's corner."""
    profile = cfg.get("layouts", {}).get(layout_id, {}) if layout_id else {}
    if corner_key in profile:
        return profile[corner_key]
    bindings = cfg.get("bindings", {})
    for key in binding_keys or []:
        if key in bindings:
            return bindings[key]
    # Once identities exist, old coordinates must not activate an unrelated display.
    if not bindings:
        return cfg.get("corners", {}).get(corner_key, "disabled")
    return "disabled"


def set_corner_action(cfg: dict[str, Any], corner_key: str, action_id: str,
                      binding_keys: list[str] | None = None, layout_id: str | None = None) -> None:
    if action_id not in _action_ids():
        raise ValueError(f"Unknown action: {action_id}")
    cfg.setdefault("corners", {})[corner_key] = action_id
    for key in binding_keys or []:
        cfg.setdefault("bindings", {})[key] = action_id
    if layout_id:
        cfg.setdefault("layouts", {}).setdefault(layout_id, {})[corner_key] = action_id


def migrate_corner_bindings(cfg: dict[str, Any], corners: list[Any], layout_id: str | None = None) -> bool:
    """Seed identities from one snapshot of legacy coordinates, only once."""
    if cfg.get("bindings") or not cfg.get("corners"):
        return False
    legacy = dict(cfg["corners"])
    migrated = False
    for corner in corners:
        if corner.key in legacy and corner.binding_keys:
            set_corner_action(cfg, corner.key, legacy[corner.key], corner.binding_keys, layout_id)
            migrated = True
    return migrated


def get_corner_options(cfg: dict[str, Any], corner: Any, layout_id: str | None = None) -> dict[str, Any]:
    """Restore exact profile options, including an explicit empty/default override."""
    profile = cfg.get("layout_options", {}).get(layout_id, {}) if layout_id else {}
    if corner.key in profile:
        return deepcopy(profile[corner.key])
    options = cfg.get("corner_options", {})
    for key in corner.binding_keys:
        if key in options:
            return deepcopy(options[key])
    return deepcopy(options.get(corner.key, {}))


def set_corner_options(cfg: dict[str, Any], corner: Any, options: dict[str, Any],
                       layout_id: str | None = None) -> None:
    values = _validated_options(options)
    for key in corner.binding_keys or [corner.key]:
        cfg.setdefault("corner_options", {})[key] = deepcopy(values)
    if layout_id:
        cfg.setdefault("layout_options", {}).setdefault(layout_id, {})[corner.key] = deepcopy(values)


_REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_VALUE = "FunCorners"


def _startup_command(target_exe: str | Path | None = None) -> str:
    if target_exe:
        return subprocess.list2cmdline([str(Path(target_exe).resolve())])
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable])
    launcher = Path(__file__).resolve().parent.parent / "run.pyw"
    if not launcher.is_file():
        raise FileNotFoundError(f"The startup launcher is missing: {launcher}")
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    if windowless.is_file():
        executable = windowless
    return subprocess.list2cmdline([str(executable), str(launcher)])


def set_auto_start(enable: bool, target_exe: str | Path | None = None) -> None:
    """Set or remove HKCU startup; failures are surfaced to the caller."""
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enable:
            winreg.SetValueEx(key, _REG_VALUE, 0, winreg.REG_SZ, _startup_command(target_exe))
        else:
            try:
                winreg.DeleteValue(key, _REG_VALUE)
            except FileNotFoundError:
                pass


def get_auto_start() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_READ) as key:
            command, _ = winreg.QueryValueEx(key, _REG_VALUE)
            return isinstance(command, str) and bool(command.strip())
    except FileNotFoundError:
        return False
