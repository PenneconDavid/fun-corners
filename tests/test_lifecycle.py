import queue
import unittest
from unittest.mock import MagicMock, patch
from fun_corners.main import AppController


class LifecycleTests(unittest.TestCase):
    def controller(self):
        app = AppController.__new__(AppController)
        app.root, app.engine, app.icon = MagicMock(), MagicMock(), MagicMock()
        app.commands = queue.Queue()
        app.window = None
        app.running = True
        app._last_tray_status = ""
        app._last_tray_active = True
        app.engine.status_snapshot.return_value = {"message": "Active"}
        return app

    def test_quit_closes_open_settings_and_stops_engine(self):
        app = self.controller()
        app.window = MagicMock()
        app.commands.put("quit")
        app.process_commands()
        self.assertFalse(app.running)
        app.window.close.assert_called_once()
        app.engine.stop.assert_called_once()
        app.icon.stop.assert_called_once()
        app.root.quit.assert_called_once()
        app.root.after.assert_not_called()

    def test_repeated_settings_requests_focus_one_window(self):
        app = self.controller()
        with patch("fun_corners.main.SettingsWindow") as window:
            app.open_settings()
            app.open_settings()
        window.assert_called_once_with(app.engine, master=app.root, block=False)
        app.window.lift_to_top.assert_called_once()

    def test_display_command_refreshes_on_main_thread(self):
        app = self.controller()
        app.commands.put("display")
        app.process_commands()
        app.engine.refresh_monitors.assert_called_once()
        app.root.after.assert_called_once()

    def test_pause_and_resume_commands(self):
        app = self.controller()
        app.commands.put("pause:15")
        app.process_commands()
        app.engine.pause_for.assert_called_once_with(15)
        app.commands.put("resume")
        with patch("fun_corners.main.cfg_mod.save"):
            app.process_commands()
        app.engine.resume.assert_called_once()

    def test_tray_failure_stops_app_instead_of_leaving_invisible_engine(self):
        app = self.controller()
        app.icon.run.side_effect = PermissionError("tray denied")
        with self.assertLogs("fun_corners.main", level="ERROR"):
            app.run_tray()
        with patch("fun_corners.main.messagebox.showerror"):
            app.process_commands()
        app.root.quit.assert_called_once()
        self.assertFalse(app.running)


if __name__ == "__main__":
    unittest.main()
