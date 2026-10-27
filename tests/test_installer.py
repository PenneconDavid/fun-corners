import base64
from pathlib import Path
import unittest
from unittest.mock import patch

import installer


class InstallerTests(unittest.TestCase):
    def test_script_uses_concrete_paths_with_safe_apostrophe_escaping(self):
        local = Path("C:/ReviewOnly") / "O'Neil"
        app = local / 'Programs' / 'FunCorners'
        start = local / 'Start Menu' / 'Fun Corners'
        desktop = local / 'Desktop' / 'Fun Corners.lnk'
        with patch.dict(installer.os.environ, {'LOCALAPPDATA': str(local)}):
            script = installer._uninstall_script(app, start, desktop)
        self.assertIn("O''Neil", script)
        self.assertNotIn('$env:APPDATA', script)
        self.assertNotIn('GetFolderPath', script)
        self.assertIn('Remove-Item -LiteralPath $start -Recurse', script)
        self.assertIn('Where-Object { $_.Path -eq $exe }', script)
        self.assertEqual(base64.b64decode(installer._encoded_command(script)).decode('utf-16-le'), script)

    def test_uninstall_refuses_other_install_targets(self):
        with self.assertRaises(ValueError):
            installer._uninstall_script(Path('C:/'), Path('C:/Fun Corners'), Path('C:/Fun Corners.lnk'))

    def test_shortcut_error_is_not_silently_ignored(self):
        with patch.object(installer.subprocess, 'run', side_effect=OSError('blocked')):
            with self.assertRaises(OSError):
                installer._create_shortcut(Path('C:/Example/app.exe'), Path('C:/Example/app.lnk'))

    def test_shortcut_command_is_encoded_and_no_window(self):
        with patch.object(installer.subprocess, 'run') as run:
            installer._create_shortcut(Path("C:/O'Neil/app.exe"), Path("C:/O'Neil/app.lnk"))
        args = run.call_args.args[0]
        self.assertEqual(args[:4], ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand'])
        script = base64.b64decode(args[4]).decode('utf-16-le')
        self.assertIn("O''Neil", script)
        self.assertTrue(run.call_args.kwargs['check'])
        self.assertEqual(run.call_args.kwargs['creationflags'], installer.subprocess.CREATE_NO_WINDOW)

    def test_running_upgrade_requests_quit_without_killing_processes(self):
        app = installer.InstallerApp.__new__(installer.InstallerApp)
        app.install_dir = Path('C:/Example/FunCorners')
        with patch.object(Path, 'exists', return_value=True), patch.object(Path, 'mkdir'), \
             patch.object(installer.shutil, 'copy2', side_effect=PermissionError('in use')), \
             patch.object(installer.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Quit Fun Corners'):
                app._perform_installation()
        run.assert_not_called()

    def test_registry_registration_error_surfaces(self):
        app = installer.InstallerApp.__new__(installer.InstallerApp)
        app.install_dir = Path('C:/Example/FunCorners')
        with patch.object(installer, '_uninstall_script', return_value='safe'), \
             patch.object(installer, '_get_special_folder', return_value=Path('C:/Example')), \
             patch.object(installer.winreg, 'CreateKey', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                app._register_uninstall(app.install_dir/'FunCorners.exe', app.install_dir/'app_icon.ico')


if __name__ == '__main__':
    unittest.main()
