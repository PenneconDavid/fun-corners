import ctypes
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from fun_corners import actions


class ActionTests(unittest.TestCase):
    def _keyboard(self, held=(), accept=None):
        state = set(held)
        history = []
        observed = []
        api = Mock()
        api.GetAsyncKeyState.side_effect = lambda key: 0x8000 if key in held else 0
        calls = [0]
        def send(count, inputs, size):
            calls[0] += 1
            n = accept if calls[0] == 1 and accept is not None else count
            for item in inputs[:n]:
                vk, up = item.ki.wVk, bool(item.ki.dwFlags & actions.KEYEVENTF_KEYUP)
                state.discard(vk) if up else state.add(vk)
                history.append((vk, up))
                if vk == actions.VK_D and not up:
                    observed.append(state.copy())
            return n
        api.SendInput.side_effect = send
        return api, state, history, observed

    def test_ctrl_trigger_does_not_create_virtual_desktop(self):
        api, state, history, observed = self._keyboard([0xA2])
        with patch.object(actions, 'user32', api):
            actions.show_desktop()
        self.assertEqual(observed, [{actions.VK_LWIN, actions.VK_D}])
        self.assertEqual(state, {0xA2})
        api.SendInput.assert_called_once()

    def test_both_held_windows_keys_restored(self):
        api, state, history, observed = self._keyboard([0x5B, 0x5C])
        with patch.object(actions, 'user32', api):
            actions.show_desktop()
        self.assertEqual(state, {0x5B, 0x5C})
        self.assertEqual(history[-2:], [(0x5B, False), (0x5C, False)])

    def test_partial_injection_releases_shortcut_and_restores_trigger(self):
        api, state, history, observed = self._keyboard([0xA2], accept=3)
        with patch.object(actions, 'user32', api):
            with self.assertRaises(OSError):
                actions.show_desktop()
        self.assertEqual(state, {0xA2})
        self.assertEqual(api.SendInput.call_count, 2)

    def test_input_structure_matches_windows_abi(self):
        self.assertEqual(ctypes.sizeof(actions.INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_display_result_is_pointer_sized(self):
        api = Mock()
        api.SendMessageTimeoutW.return_value = 1
        with patch.object(actions, 'user32', api):
            actions.sleep_displays()
        result = api.SendMessageTimeoutW.call_args.args[-1]._obj
        self.assertEqual(ctypes.sizeof(result), ctypes.sizeof(ctypes.c_void_p))

    def test_display_failure_is_reported(self):
        api = Mock()
        api.SendMessageTimeoutW.return_value = 0
        with patch.object(actions, 'user32', api), self.assertRaises(OSError):
            actions.sleep_displays()

    def test_shortcut_validation(self):
        self.assertEqual(actions.parse_shortcut('Ctrl+Alt+K'), (0xA2, 0xA4, ord('K')))
        self.assertEqual(actions.parse_shortcut('Win+F24'), (0x5B, 0x87))
        for value in ('', 'Ctrl++K', 'Ctrl+Ctrl+A', 'Ctrl+F25', 'Alt+banana', 'Ctrl+Win'):
            self.assertIsNotNone(actions.validate_shortcut(value), value)
        self.assertIsNone(actions.validate_shortcut('Shift+PageDown'))

    def test_custom_shortcut_dispatch(self):
        with patch.object(actions, '_press_keys') as press:
            actions.execute('custom_shortcut', {'shortcut': 'Ctrl+Alt+K'})
        press.assert_called_once_with(0xA2, 0xA4, ord('K'))

    def test_app_launch_is_explicit_and_never_uses_shell(self):
        exe = Path('C:/ReviewOnly/example.exe')
        with patch.object(Path, 'is_file', return_value=True), patch.object(actions.subprocess, 'Popen') as launch:
            actions.execute('launch_app', {'app_path': str(exe)})
        launch.assert_called_once_with([str(exe)], cwd=str(exe.parent), shell=False)
        for path in ('notepad.exe & calc.exe', 'relative.exe', 'https://example.com', 'C:/missing.exe'):
            with self.assertRaises(ValueError):
                actions.execute('launch_app', {'app_path': path})


if __name__ == '__main__':
    unittest.main()
