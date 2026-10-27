from copy import deepcopy
import ctypes
import ctypes.wintypes as wt
import unittest
import threading
from unittest.mock import MagicMock, patch

from fun_corners import config, corners
from fun_corners.monitor import Monitor


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.enumeration = patch.object(corners, "get_monitors", return_value=[Monitor(0, 0, 1000, 800, "A", True, device_id="panel-a")])
        self.enumeration.start()
        self.addCleanup(self.enumeration.stop)
        self.native = patch.object(corners, "_user32")
        self.user32 = self.native.start()
        self.addCleanup(self.native.stop)
        self.user32.GetForegroundWindow.return_value = 123
        self.user32.GetAsyncKeyState.return_value = 0
        self.cfg = deepcopy(config.DEFAULTS)
        self.cfg["corners"] = {"0,0": "show_desktop"}
        self.engine = corners.CornerEngine(self.cfg)
        self.dispatch = patch.object(corners.actions, "execute")
        self.execute = self.dispatch.start()
        self.addCleanup(self.dispatch.stop)

    def sample(self, t, position=(0, 0)):
        self.engine._poll_once(t, position)
        return self.engine.status_snapshot()

    def test_preview_reaches_completion_without_dispatching(self):
        self.engine.preview_mode = True
        self.assertEqual(self.sample(10)["progress"], 0)
        self.assertAlmostEqual(self.sample(10.15)["progress"], .5)
        self.assertEqual(self.sample(10.31)["state"], "preview_fired")
        self.assertEqual(self.sample(10.5)["state"], "preview_fired")
        self.execute.assert_not_called()

    def test_action_fires_once_then_requires_exit_and_cooldown(self):
        self.sample(10)
        self.sample(10.31)
        self.sample(20)
        self.execute.assert_called_once_with("show_desktop", {})
        self.sample(21, (100, 100))
        self.sample(22)
        self.sample(22.31)
        self.assertEqual(self.execute.call_count, 2)

    def test_per_corner_tuning_overrides_global_defaults(self):
        config.set_corner_options(self.cfg, self.engine.corners[0], {"dwell_ms": 800, "modifier_key": "ctrl"})
        self.assertEqual(self.sample(10)["state"], "modifier")
        self.user32.GetAsyncKeyState.return_value = 0x8000
        self.sample(11)
        self.sample(11.4)
        self.execute.assert_not_called()
        self.sample(11.81)
        self.execute.assert_called_once_with("show_desktop", {"dwell_ms": 800, "modifier_key": "ctrl"})

    def test_pause_expires_without_carrying_over_dwell(self):
        self.sample(10)
        with patch.object(corners.time, "monotonic", return_value=10.1):
            self.engine.pause_for(5)
        self.assertEqual(self.sample(300)["state"], "paused")
        self.assertEqual(self.sample(311)["progress"], 0)
        self.execute.assert_not_called()
        self.sample(311.31)
        self.execute.assert_called_once()

    def test_manual_pause_survives_temporary_pause_expiry(self):
        self.engine.enabled = False
        with patch.object(corners.time, "monotonic", return_value=10):
            self.engine.pause_for(5)
        self.assertEqual(self.sample(400)["state"], "paused")
        self.engine.resume()
        self.assertEqual(self.sample(401)["state"], "dwelling")

    def test_disabling_and_reenabling_resets_dwell(self):
        self.sample(10)
        self.engine.enabled = False
        self.sample(11)
        self.engine.enabled = True
        self.sample(12)
        self.execute.assert_not_called()

    def test_excluded_foreground_app_suppresses_action(self):
        self.cfg["excluded_apps"] = ["game.exe"]
        with patch.object(corners, "_foreground_executable", return_value="game.exe"):
            self.assertEqual(self.sample(10)["state"], "excluded")
            self.sample(20)
        self.execute.assert_not_called()

    def test_fullscreen_suppression_resets_dwell(self):
        self.cfg["ignore_fullscreen"] = True
        with patch.object(corners, "_is_fullscreen_active", return_value=True):
            self.assertEqual(self.sample(10)["state"], "fullscreen")
            self.sample(20)
        self.execute.assert_not_called()
        with patch.object(corners, "_is_fullscreen_active", return_value=False):
            self.assertEqual(self.sample(21)["progress"], 0)

    def test_action_failure_is_visible_and_not_retried_in_place(self):
        self.execute.side_effect = OSError("blocked")
        self.sample(10)
        with self.assertLogs(corners._log, level="ERROR"):
            status = self.sample(10.31)
        self.assertEqual(status["state"], "error")
        self.assertIn("blocked", status["message"])
        self.sample(20)
        self.execute.assert_called_once()

    def test_monitor_refresh_keeps_old_snapshot_and_updates_version(self):
        monitors, old, version = self.engine.snapshot_layout()
        with patch.object(corners, "get_monitors", return_value=[Monitor(0, 0, 1200, 900, "A", True, device_id="panel-a")]):
            self.engine.refresh_monitors()
        self.assertGreater(self.engine.topology_version, version)
        self.assertEqual(old[-1].x, 1000)
        self.assertEqual(self.engine.corners[-1].x, 1200)
        self.assertEqual(self.engine.corners[0].action, "show_desktop")

    def test_apply_preserves_shared_config_reference_and_preview(self):
        self.engine.preview_mode = True
        self.engine.apply_config(self.cfg)
        self.assertIs(self.engine.config, self.cfg)
        self.assertEqual(self.cfg["dwell_ms"], 300)
        self.assertTrue(self.engine.preview_mode)

    def test_apply_uses_cached_topology_when_windows_enumeration_unavailable(self):
        candidate = deepcopy(self.cfg)
        candidate["dwell_ms"] = 600
        with patch.object(corners, "get_monitors", side_effect=OSError("display transient")):
            self.engine.apply_config(candidate)
        self.assertEqual(self.cfg["dwell_ms"], 600)
        self.assertEqual(self.engine.corners[0].action, "show_desktop")

    def test_slow_action_does_not_block_status_preview_or_pause(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            release.wait(3)
        self.execute.side_effect = slow
        self.sample(10)
        worker = threading.Thread(target=lambda: self.sample(10.31))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            completed = threading.Event()
            def controls():
                self.engine.status_snapshot()
                self.engine.preview_mode = True
                self.engine.pause_for(5)
                completed.set()
            controls_thread = threading.Thread(target=controls)
            controls_thread.start()
            self.assertTrue(completed.wait(1), "Native action held engine state lock")
            controls_thread.join(1)
        finally:
            release.set()
            worker.join(2)

    def test_layout_restores_options_used_by_actual_dispatch(self):
        panel_a = self.engine.monitors[0]
        panel_b = Monitor(1000, 0, 1000, 800, "B", False, device_id="panel-b")
        undocked = corners.layout_key([panel_a])
        docked = corners.layout_key([panel_a, panel_b])
        corner = self.engine.corners[0]
        config.set_corner_action(self.cfg, corner.key, "custom_shortcut", corner.binding_keys, docked)
        config.set_corner_options(self.cfg, corner, {"shortcut": "Ctrl+K", "dwell_ms": 1000}, docked)
        config.set_corner_action(self.cfg, corner.key, "custom_shortcut", corner.binding_keys, undocked)
        config.set_corner_options(self.cfg, corner, {"shortcut": "Ctrl+M", "dwell_ms": 50}, undocked)
        with patch.object(corners, "get_monitors", return_value=[panel_a, panel_b]):
            self.engine.refresh_monitors()
        self.sample(10)
        self.sample(10.1)
        self.execute.assert_not_called()
        self.sample(11.01)
        self.execute.assert_called_once_with("custom_shortcut", {"shortcut": "Ctrl+K", "dwell_ms": 1000})

    def test_disabled_corners_show_unassigned(self):
        self.engine.corners = []
        self.assertEqual(self.sample(10)["state"], "unassigned")

    def test_maximized_window_is_not_treated_as_fullscreen(self):
        self.user32.GetDesktopWindow.return_value = 1
        self.user32.GetShellWindow.return_value = 2
        self.user32.IsZoomed.return_value = True
        self.assertFalse(corners._is_fullscreen_active(self.engine.monitors, 123))

    def test_borderless_monitor_sized_window_is_fullscreen(self):
        self.user32.GetDesktopWindow.return_value = 1
        self.user32.GetShellWindow.return_value = 2
        self.user32.IsZoomed.return_value = False
        def fill(_hwnd, ptr):
            rect = ctypes.cast(ptr, ctypes.POINTER(wt.RECT)).contents
            rect.left, rect.top, rect.right, rect.bottom = 0, 0, 1000, 800
            return 1
        self.user32.GetWindowRect.side_effect = fill
        self.assertTrue(corners._is_fullscreen_active(self.engine.monitors, 123))


if __name__ == "__main__":
    unittest.main()
