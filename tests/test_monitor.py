import unittest
from unittest.mock import patch

from fun_corners import monitor
from fun_corners.monitor import Monitor, find_corners, layout_key


class MonitorTests(unittest.TestCase):
    def make(self, x=0, y=0, w=1920, h=1080, identity="A", primary=True):
        return Monitor(x, y, w, h, identity, primary, device_id=identity)

    def test_common_topologies(self):
        cases = [
            ([self.make()], 4, 4),
            ([self.make(), self.make(x=1920, identity="B", primary=False)], 4, 4),
            ([self.make(), self.make(y=-1080, identity="B", primary=False)], 4, 4),
            ([self.make(), self.make(x=1920, y=200, identity="B", primary=False)], 8, 6),
        ]
        for monitors, all_count, outer_count in cases:
            with self.subTest(monitors=monitors):
                self.assertEqual(len(find_corners(monitors)), all_count)
                self.assertEqual(len(find_corners(monitors, ignore_inner=True)), outer_count)

    def test_negative_coordinates_and_resolution_change_keep_binding(self):
        before = find_corners([self.make()])
        after = find_corners([self.make(x=-2560, y=-1440, w=2560, h=1440)])
        self.assertEqual(before[0].binding_keys, after[0].binding_keys)
        self.assertNotEqual(before[0].key, after[0].key)

    def test_layout_fingerprint_is_order_independent_but_geometry_sensitive(self):
        a = self.make()
        b = self.make(x=1920, identity="B", primary=False)
        self.assertEqual(layout_key([a, b]), layout_key([b, a]))
        moved = self.make(x=-1920, identity="B", primary=False)
        self.assertNotEqual(layout_key([a, b]), layout_key([a, moved]))

    def test_distinct_device_identity_avoids_same_coordinate_collision(self):
        a = find_corners([self.make(identity="A")])[0]
        b = find_corners([self.make(identity="B")])[0]
        self.assertEqual(a.key, b.key)
        self.assertNotEqual(a.binding_keys, b.binding_keys)

    def test_saddle_corner_has_both_monitor_bindings(self):
        corners = find_corners([self.make(), self.make(x=1920, y=1080, identity="B", primary=False)])
        shared = next(c for c in corners if c.key == "1920,1080")
        self.assertEqual(set(shared.binding_keys), {"A|bottom-right", "B|top-left"})

    def test_enumeration_failure_is_not_mistaken_for_empty_layout(self):
        with patch.object(monitor._user32, "EnumDisplayMonitors", return_value=0):
            with self.assertRaises(OSError):
                monitor.get_monitors()

    def test_adapter_name_is_fallback_if_device_interface_unavailable(self):
        with patch.object(monitor._user32, "EnumDisplayDevicesW", return_value=0):
            self.assertEqual(monitor._display_identity("DISPLAY-A"), "display-a")


if __name__ == "__main__":
    unittest.main()
