"""Settings regression checks without desktop, registry, or configuration writes."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fun_corners import config
from fun_corners.monitor import Corner
from fun_corners.settings_ui import SettingsWindow, _excluded_apps, _number


class Value:
    def __init__(self, value):
        self.value = value

    def get(self, *_args):
        return self.value

    def set(self, value):
        self.value = value


def window_fixture():
    window = SettingsWindow.__new__(SettingsWindow)
    window.engine = SimpleNamespace(config=deepcopy(config.DEFAULTS),
                                    apply_config=MagicMock(), preview_mode=False)
    window._corners = [Corner(0, 0, label="Top-left", binding_keys=["display-a:tl"])]
    window._corner_edits = {"0,0": {"action": "show_desktop", "dwell": "", "modifier": "Inherit",
                                   "shortcut": "", "app_path": ""}}
    window._layout_id = "test-layout"
    window._dwell_var = Value("300")
    window._size_var = Value("6")
    window._cooldown_var = Value("1500")
    window._mod_var = Value("None")
    window._fullscreen_var = Value(False)
    window._inner_var = Value(True)
    window._autostart_var = Value(False)
    window._exclusions = Value("")
    window._saving = False
    window._closed = False
    window._btn_save = MagicMock()
    window._notice_var = Value("")
    window._on_save = None
    window.root = MagicMock()
    return window


class SettingsTests(unittest.TestCase):
    def test_compact_height_hides_map_and_normal_height_restores_it(self):
        window = window_fixture()
        window.canvas = MagicMock()
        window._notice = MagicMock()
        window._status_label = MagicMock()
        window._layout_hint = MagicMock()
        window._topology_notice = MagicMock()
        window._map_compact = False
        window._monitors = [object()]
        window._layout_hint_var = Value("")
        window._window_resized(SimpleNamespace(widget=window.root, width=580, height=490))
        window.canvas.grid_remove.assert_called_once()
        self.assertTrue(window._map_compact)
        self.assertIn("list below", window._layout_hint_var.value)
        # Repeated Configure messages must not keep changing the geometry.
        window._window_resized(SimpleNamespace(widget=window.root, width=580, height=490))
        window.canvas.grid_remove.assert_called_once()
        window._window_resized(SimpleNamespace(widget=window.root, width=860, height=800))
        window.canvas.grid.assert_called_once_with()
        self.assertFalse(window._map_compact)
        self.assertIn("numbered corner", window._layout_hint_var.value)

    def test_child_resize_does_not_change_compact_layout(self):
        window = window_fixture()
        window.canvas = MagicMock()
        window._map_compact = False
        window._window_resized(SimpleNamespace(widget=object(), width=10, height=10))
        window.canvas.grid_remove.assert_not_called()
        self.assertFalse(window._map_compact)

    def test_numeric_validation_rejects_invalid_and_out_of_range(self):
        for value in ("", "slow", "49", "5001", "300.5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _number(value, "Dwell", 50, 5000)
        self.assertEqual(_number("300", "Dwell", 50, 5000), 300)

    def test_exclusions_are_validated_and_deduplicated(self):
        self.assertEqual(_excluded_apps("Game.exe\ngame.exe,slides.exe"), ["game.exe", "slides.exe"])
        for value in ("*.exe", "C:/Games/game.exe", "game", "../game.exe"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _excluded_apps(value)

    def test_hotplug_save_uses_opening_snapshot_and_preserves_other_bindings(self):
        window = window_fixture()
        window.engine.corners = [Corner(2000, 0, binding_keys=["new-monitor:tl"])]
        window.engine.config["bindings"] = {"unplugged:br": "lock"}
        before = deepcopy(window.engine.config)
        candidate = window._prepare_config()
        self.assertEqual(candidate["bindings"]["display-a:tl"], "show_desktop")
        self.assertEqual(candidate["bindings"]["unplugged:br"], "lock")
        self.assertNotIn("2000,0", candidate["corners"])
        self.assertEqual(window.engine.config, before)

    def test_per_corner_overrides_and_global_exclusion_are_saved(self):
        window = window_fixture()
        window._corner_edits["0,0"].update(dwell="1200", modifier="Ctrl")
        window._exclusions.value = "game.exe"
        result = window._prepare_config()
        self.assertEqual(result["corner_options"]["display-a:tl"], {"dwell_ms": 1200, "modifier_key": "ctrl"})
        self.assertEqual(result["excluded_apps"], ["game.exe"])

    def test_custom_shortcut_rejected_before_any_system_change(self):
        window = window_fixture()
        window._corner_edits["0,0"].update(action="custom_shortcut", shortcut="Ctrl+NoSuchKey")
        with patch.object(config, "save") as save, patch.object(config, "set_auto_start") as start, \
             patch("fun_corners.settings_ui.messagebox.showerror"):
            window._on_save_click()
        save.assert_not_called()
        start.assert_not_called()
        window.engine.apply_config.assert_not_called()
        self.assertFalse(window._saving)
        window._btn_save.configure.assert_called_with(state="normal", text="Save & apply")

    def test_save_error_restores_button_and_keeps_live_settings_unchanged(self):
        window = window_fixture()
        window._autostart_var.value = True
        with patch.object(config, "get_auto_start", return_value=False), \
             patch.object(config, "save", side_effect=OSError("disk full")), \
             patch.object(config, "set_auto_start") as start, \
             patch("fun_corners.settings_ui.messagebox.showerror"):
            window._on_save_click()
        self.assertEqual([c.args[0] for c in start.call_args_list], [True, False])
        window.engine.apply_config.assert_not_called()
        self.assertFalse(window._saving)
        self.assertIn("disk full", window._notice_var.value)

    def test_success_saves_then_applies_and_retains_safe_preview(self):
        window = window_fixture()
        window.engine.preview_mode = True
        order = []
        window.engine.apply_config.side_effect = lambda _cfg: order.append("apply")
        with patch.object(config, "get_auto_start", return_value=False), \
             patch.object(config, "save", side_effect=lambda _cfg: order.append("save")), \
             patch.object(config, "set_auto_start") as start:
            window._on_save_click()
        self.assertEqual(order, ["save", "apply"])
        start.assert_not_called()
        self.assertTrue(window.engine.preview_mode)
        self.assertFalse(window._closed)

    def test_close_cancels_preview_and_status_timer_exactly_once(self):
        window = window_fixture()
        window.engine.preview_mode = True
        window._status_after = "status-timer"
        window.close()
        window.close()
        self.assertFalse(window.engine.preview_mode)
        window.root.after_cancel.assert_called_once_with("status-timer")
        window.root.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
