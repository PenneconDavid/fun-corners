"""Windows entry point. Tk and mutable UI state belong to the main thread."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import logging
from logging.handlers import RotatingFileHandler
import queue
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Callable

from . import config as cfg_mod
from .corners import CornerEngine
from .settings_ui import SettingsWindow
from .tray import create_tray, update_status

_log = logging.getLogger(__name__)
_MUTEX_NAME = "Local\\FunCorners_App_SingleInstance_Mutex"
_ERROR_ALREADY_EXISTS = 183


def _set_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        ctypes.windll.user32.SetProcessDPIAware()


def _start_display_listener(on_display_change: Callable[[], None]) -> Callable[[], None]:
    """Broadcast-capable hidden window; callbacks only enqueue main-thread work."""
    user32 = ctypes.windll.user32
    LRESULT, WPARAM, LPARAM = ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                    ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wt.WORD
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user32.CreateWindowExW.restype = wt.HWND
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, WPARAM, LPARAM]
    user32.PostMessageW.restype = wt.BOOL
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.DestroyWindow.restype = wt.BOOL
    user32.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
    user32.UnregisterClassW.restype = wt.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = wt.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    window: list[int | None] = [None]
    ready = threading.Event()
    stop_requested = threading.Event()

    def loop() -> None:
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == 0x007E:  # WM_DISPLAYCHANGE
                on_display_change()
            elif msg == 0x0010:  # WM_CLOSE
                user32.DestroyWindow(hwnd)
                return 0
            elif msg == 0x0002:  # WM_DESTROY
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc = WNDPROC(wndproc)
        cls = WNDCLASSW()
        cls.lpfnWndProc = proc
        cls.lpszClassName = "FunCornersDisplayListener"
        registered = user32.RegisterClassW(ctypes.byref(cls))
        try:
            if not registered:
                raise OSError("Could not register display listener")
            window[0] = user32.CreateWindowExW(0, cls.lpszClassName, "DisplayListener", 0,
                                               0, 0, 0, 0, None, None, None, None)
            if not window[0]:
                raise OSError("Could not create display listener")
            ready.set()
            if stop_requested.is_set():
                user32.PostMessageW(window[0], 0x0010, 0, 0)
            msg = wt.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            _log.exception("Display listener failed")
        finally:
            window[0] = None
            ready.set()
            if registered:
                user32.UnregisterClassW(cls.lpszClassName, None)

    thread = threading.Thread(target=loop, daemon=True, name="FunCorners-DisplayListener")
    thread.start()

    def stop() -> None:
        stop_requested.set()
        ready.wait(1)
        if window[0]:
            user32.PostMessageW(window[0], 0x0010, 0, 0)
        thread.join(timeout=2)
    return stop


class AppController:
    """All tray callbacks queue commands; the persistent Tk loop consumes them."""
    def __init__(self, root: tk.Tk, engine: CornerEngine) -> None:
        self.root, self.engine = root, engine
        self.commands: queue.Queue[str] = queue.Queue()
        self.window: SettingsWindow | None = None
        self.running = True
        self.icon = create_tray(engine, engine.config,
                                on_settings=lambda: self.commands.put("settings"),
                                on_quit=lambda: self.commands.put("quit"),
                                on_command=self.commands.put)
        self._last_tray_status = ""
        self._last_tray_active: bool | None = None

    def run_tray(self) -> None:
        try:
            self.icon.run()
        except Exception as exc:
            _log.exception("System tray failed")
            self.commands.put(f"tray-error:{exc}")

    def open_settings(self) -> None:
        if self.window and self.window.root.winfo_exists():
            self.window.lift_to_top()
        else:
            self.window = SettingsWindow(self.engine, master=self.root, block=False)

    def process_commands(self) -> None:
        if not self.running:
            return
        try:
            while True:
                command = self.commands.get_nowait()
                if command == "quit":
                    self.running = False
                    self.engine.stop()
                    if self.window and self.window.root.winfo_exists():
                        self.window.close()
                    self.icon.stop()
                    self.root.quit()
                    return
                if command == "settings":
                    self.open_settings()
                elif command == "display":
                    self.engine.refresh_monitors()
                elif command in ("toggle", "resume"):
                    if command == "toggle":
                        self.engine.enabled = not self.engine.enabled
                    else:
                        self.engine.resume()
                    cfg_mod.save(self.engine.config)
                elif command.startswith("pause:"):
                    self.engine.pause_for(int(command.split(":")[1]))
                elif command.startswith("tray-error:"):
                    self.engine.stop()
                    messagebox.showerror("Fun Corners", "The system tray could not start. Fun Corners will exit.\n\n" + command.split(":", 1)[1], parent=self.root)
                    self.commands.put("quit")
        except queue.Empty:
            pass
        except Exception as exc:
            _log.exception("App command failed")
            self.engine.report_error(str(exc))
            messagebox.showerror("Fun Corners", str(exc), parent=self.root)
        status = self.engine.status_snapshot()
        message = status.get("message", "Active")
        if message != self._last_tray_status:
            self.icon.title = f"Fun Corners — {message}"[:127]
            self.icon.update_menu()
            self._last_tray_status = message
        active = status.get("state") not in ("paused", "fullscreen", "excluded", "unassigned", "error", "unavailable")
        if active != self._last_tray_active:
            update_status(self.icon, active)
            self._last_tray_active = active
        self.root.after(100, self.process_commands)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fun Corners for Windows")
    parser.add_argument("--preview", action="store_true", help="Open Settings in safe preview; no actions run while preview is on")
    args = parser.parse_args()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
    kernel32.CreateMutexW.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL
    mutex = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    error = ctypes.get_last_error()
    if not mutex:
        raise ctypes.WinError(error)
    _set_dpi_aware()
    root = tk.Tk()
    root.withdraw()
    if error == _ERROR_ALREADY_EXISTS:
        messagebox.showinfo("Fun Corners", "Fun Corners is already running. Open Settings from its system tray icon.", parent=root)
        root.destroy()
        kernel32.CloseHandle(mutex)
        return
    engine = None
    controller = None
    stop_listener = lambda: None
    try:
        handler = RotatingFileHandler(cfg_mod._config_dir()/"fun-corners.log", maxBytes=256_000, backupCount=2, encoding="utf-8")
        logging.basicConfig(level=logging.WARNING, handlers=[handler])
        root.report_callback_exception = lambda typ, exc, tb: (
            _log.error("UI callback failed", exc_info=(typ, exc, tb)),
            messagebox.showerror("Fun Corners", f"The operation failed: {exc}", parent=root))
        config = cfg_mod.load()
        engine = CornerEngine(config)
        engine.preview_mode = args.preview
        controller = AppController(root, engine)
        stop_listener = _start_display_listener(lambda: controller.commands.put("display"))
        engine.start()
        tray_thread = threading.Thread(target=controller.run_tray, daemon=True, name="FunCorners-tray")
        tray_thread.start()
        if args.preview or not any(c.action != "disabled" for c in engine.corners):
            controller.commands.put("settings")
        root.after(0, controller.process_commands)
        root.mainloop()
    finally:
        if engine:
            engine.stop()
        stop_listener()
        if controller:
            controller.icon.stop()
        try:
            root.destroy()
        except tk.TclError:
            pass
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
