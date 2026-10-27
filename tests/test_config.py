import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fun_corners import config
from fun_corners.monitor import Monitor, find_corners, layout_key


class ConfigTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(dir=test_root, prefix="config-test-")
        assert Path(self.temp.name).resolve().is_relative_to(test_root)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.path_patch = patch.object(config, "_config_path", return_value=self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(setattr, config, "_invalid_source", None)

    def test_valid_json_with_wrong_types_loads_safe_defaults(self):
        for value in (None, [], {"corners": None}, {"dwell_ms": "slow"},
                      {"dwell_ms": True}, {"corner_options": {"x": {"modifier_key": []}}}):
            with self.subTest(value=value):
                self.path.write_text(json.dumps(value), encoding="utf-8")
                loaded = config.load()
                self.assertIsInstance(loaded["corners"], dict)
                self.assertIs(type(loaded["dwell_ms"]), int)
                self.assertGreaterEqual(loaded["dwell_ms"], 50)
                self.assertTrue(config.load_warnings)

    def test_corrupt_original_is_untouched_on_load_and_backed_up_on_save(self):
        original = b'{invalid json'
        self.path.write_bytes(original)
        loaded = config.load()
        self.assertEqual(self.path.read_bytes(), original)
        config.save(loaded)
        backups = list(self.path.parent.glob("config.before-repair-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(json.loads(self.path.read_text())["dwell_ms"], 300)

    def test_atomic_replace_failure_keeps_previous_config(self):
        original = b'{"enabled": false}'
        self.path.write_bytes(original)
        with patch.object(config.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                config.save(copy.deepcopy(config.DEFAULTS))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_inaccessible_settings_location_loads_defaults_and_warning(self):
        with patch.object(config, "_config_path", side_effect=PermissionError("denied")):
            loaded = config.load()
        self.assertEqual(loaded, config.DEFAULTS)
        self.assertTrue(config.load_warnings)

    def test_defaults_are_not_shared_between_loads(self):
        first = config.load()
        first["corners"]["0,0"] = "sleep_displays"
        first["excluded_apps"].append("game.exe")
        self.assertEqual(config.load()["corners"], {})
        self.assertEqual(config.load()["excluded_apps"], [])

    def test_validates_ranges_actions_and_executable_exclusions(self):
        warnings = []
        loaded = config.validate({"corner_size": -2, "cooldown_ms": 1,
                                  "modifier_key": "bogus", "corners": {"0,0": "unknown"},
                                  "excluded_apps": [" GAME.EXE ", "game.exe"]}, warnings)
        self.assertEqual(loaded["corner_size"], 6)
        self.assertEqual(loaded["cooldown_ms"], 1500)
        self.assertEqual(loaded["modifier_key"], "none")
        self.assertEqual(loaded["corners"], {})
        self.assertEqual(loaded["excluded_apps"], ["game.exe"])
        self.assertTrue(warnings)

    def test_legacy_migration_snapshots_all_four_corners(self):
        monitors = [Monitor(0, 0, 1920, 1080, "display", True, device_id="physical-A")]
        corners = find_corners(monitors)
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["corners"] = {c.key: "sleep_displays" for c in corners}
        topology = layout_key(monitors)
        self.assertTrue(config.migrate_corner_bindings(cfg, corners, topology))
        self.assertEqual(len(cfg["bindings"]), 4)
        self.assertEqual(len(cfg["layouts"][topology]), 4)
        self.assertTrue(all(config.get_corner_action(cfg, c.key, c.binding_keys, topology) == "sleep_displays" for c in corners))
        self.assertFalse(config.migrate_corner_bindings(cfg, corners, topology))

    def test_assignment_follows_monitor_but_not_replacement_at_same_coordinates(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        corner = find_corners([Monitor(0, 0, 1920, 1080, "display", True, device_id="A")])[0]
        config.set_corner_action(cfg, corner.key, "lock", corner.binding_keys)
        moved = find_corners([Monitor(-2560, 0, 2560, 1440, "display", True, device_id="A")])[0]
        self.assertEqual(config.get_corner_action(cfg, moved.key, moved.binding_keys), "lock")
        replacement = find_corners([Monitor(0, 0, 1920, 1080, "display", True, device_id="B")])[0]
        self.assertEqual(config.get_corner_action(cfg, replacement.key, replacement.binding_keys), "disabled")

    def test_docked_profile_restores_layout_specific_action(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        config.set_corner_action(cfg, "0,0", "lock", ["A|top-left"], "docked")
        config.set_corner_action(cfg, "0,0", "sleep_displays", ["A|top-left"], "undocked")
        self.assertEqual(config.get_corner_action(cfg, "0,0", ["A|top-left"], "docked"), "lock")
        self.assertEqual(config.get_corner_action(cfg, "0,0", ["A|top-left"], "undocked"), "sleep_displays")

    def test_per_corner_options_follow_identity_and_return_copies(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        corner = find_corners([Monitor(0, 0, 100, 100, "A", True)])[0]
        config.set_corner_options(cfg, corner, {"dwell_ms": 750, "modifier_key": "ctrl", "untrusted": 12})
        self.assertEqual(config.get_corner_options(cfg, corner), {"dwell_ms": 750, "modifier_key": "ctrl"})
        options = config.get_corner_options(cfg, corner)
        options["dwell_ms"] = 99
        self.assertEqual(config.get_corner_options(cfg, corner)["dwell_ms"], 750)

    def test_layout_profiles_restore_actions_and_all_custom_options(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        corner = find_corners([Monitor(0, 0, 100, 100, "A", True, device_id="A")])[0]
        docked = {"shortcut": "Ctrl+Alt+K", "dwell_ms": 850, "modifier_key": "shift"}
        undocked = {"app_path": "C:/Applications/example.exe", "dwell_ms": 500, "modifier_key": "ctrl"}
        config.set_corner_action(cfg, corner.key, "custom_shortcut", corner.binding_keys, "docked")
        config.set_corner_options(cfg, corner, docked, "docked")
        config.set_corner_action(cfg, corner.key, "launch_app", corner.binding_keys, "undocked")
        config.set_corner_options(cfg, corner, undocked, "undocked")
        config.save(cfg)
        restored = config.load()
        self.assertEqual(config.get_corner_action(restored, corner.key, corner.binding_keys, "docked"), "custom_shortcut")
        self.assertEqual(config.get_corner_options(restored, corner, "docked"), docked)
        self.assertEqual(config.get_corner_action(restored, corner.key, corner.binding_keys, "undocked"), "launch_app")
        self.assertEqual(config.get_corner_options(restored, corner, "undocked"), undocked)
        self.assertEqual(config.get_corner_options(restored, corner, "new-topology"), undocked)

    def test_explicit_empty_layout_options_override_binding_options(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        corner = find_corners([Monitor(0, 0, 100, 100, "A", True)])[0]
        config.set_corner_options(cfg, corner, {}, "docked")
        config.set_corner_options(cfg, corner, {"dwell_ms": 900}, "undocked")
        self.assertEqual(config.get_corner_options(cfg, corner, "docked"), {})
        self.assertEqual(config.get_corner_options(cfg, corner, "undocked"), {"dwell_ms": 900})

    def test_untrusted_custom_options_are_repaired_on_load(self):
        self.path.write_text(json.dumps({
            "corner_options": {"A|top-left": {"shortcut": "Ctrl+Bogus", "app_path": 19}},
            "layout_options": {"docked": {"0,0": {"shortcut": "Ctrl++X", "dwell_ms": -10}}},
        }), encoding="utf-8")
        restored = config.load()
        self.assertEqual(restored["corner_options"]["A|top-left"], {})
        self.assertEqual(restored["layout_options"]["docked"]["0,0"], {})
        self.assertTrue(config.load_warnings)

    def test_source_startup_uses_launcher_not_module_main_script(self):
        with patch.object(config.sys, "argv", ["C:/somewhere/fun_corners/__main__.py"]):
            command = config._startup_command()
        self.assertIn("run.pyw", command)
        self.assertNotIn("__main__.py", command)
        self.assertIn(str(Path(config.__file__).resolve().parent.parent / "run.pyw"), command)

    def test_registry_failure_is_reported_to_caller(self):
        import winreg
        with patch.object(winreg, "CreateKeyEx", side_effect=PermissionError("blocked")):
            with self.assertRaises(PermissionError):
                config.set_auto_start(True)

    def test_startup_changes_are_mocked_and_command_is_quoted(self):
        import winreg
        with patch.object(winreg, "CreateKeyEx") as create, patch.object(winreg, "SetValueEx") as set_value:
            config.set_auto_start(True, target_exe=Path(self.temp.name) / "Folder With Spaces" / "FunCorners.exe")
        self.assertTrue(create.called)
        self.assertIn('"', set_value.call_args.args[-1])
        self.assertIn("FunCorners.exe", set_value.call_args.args[-1])


if __name__ == "__main__":
    unittest.main()
