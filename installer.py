"""Fun Corners — Windows Installer & Setup Wizard.

Handles:
1. Installing Fun Corners to %LOCALAPPDATA%\\Programs\\FunCorners
2. Registering Fun Corners in Windows "Installed apps" (Programs and Features / Add/Remove Programs)
3. Creating Start Menu shortcut (appears in Windows Search & Start list)
4. Optional Desktop shortcut checkbox
5. Optional "Launch now" checkbox
6. Uninstaller generation (clean uninstallation support)
"""

from __future__ import annotations

import base64
import ctypes
import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox
import winreg
from pathlib import Path

APP_NAME = "Fun Corners"
APP_PUBLISHER = "Fun Corners"
APP_VERSION = "1.0.0"

_BG = "#f3f3f3"
_ACCENT = "#0078d4"
_ACCENT_HI = "#1a8cff"


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _encoded_command(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _create_shortcut(target_path: Path, shortcut_path: Path, icon_path: Path | None = None, description: str = "") -> None:
    """Create the requested shortcut, surfacing failure to the setup wizard."""
    icon = f"$s.IconLocation = {_ps_literal(icon_path)}; " if icon_path and icon_path.exists() else ""
    script = (
        "$ErrorActionPreference = 'Stop'; $ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut({_ps_literal(shortcut_path)}); "
        f"$s.TargetPath = {_ps_literal(target_path)}; "
        f"$s.WorkingDirectory = {_ps_literal(target_path.parent)}; {icon}"
        f"$s.Description = {_ps_literal(description or APP_NAME)}; $s.Save()"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", _encoded_command(script)],
                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


def _uninstall_script(install_dir: Path, start_folder: Path, desktop_shortcut: Path) -> str:
    """Build a self-contained uninstaller for concrete, validated locations.

    Keep preferences in APPDATA. Stop only the installed executable, never a
    different portable copy or an unrelated process with the same name.
    """
    expected = (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) /
                "Programs" / "FunCorners").resolve()
    if install_dir.resolve() != expected or install_dir.name != "FunCorners":
        raise ValueError("Refusing to generate an uninstaller outside the FunCorners installation directory.")
    if start_folder.name != "Fun Corners" or desktop_shortcut.name != "Fun Corners.lnk":
        raise ValueError("Unexpected shortcut cleanup paths.")
    return (
        "$ErrorActionPreference = 'Stop'; "
        f"$install = {_ps_literal(install_dir.resolve())}; "
        f"$expected = {_ps_literal(expected)}; "
        "if ([IO.Path]::GetFullPath($install) -ne $expected) { throw 'Invalid installation path' }; "
        f"$exe = {_ps_literal(install_dir.resolve() / 'FunCorners.exe')}; "
        "$processes = @(Get-Process -Name FunCorners -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Path -eq $exe }); "
        "if ($processes.Count) { $processes | Stop-Process -Force; "
        "$processes | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue }; "
        "if (Test-Path -LiteralPath $install) { Remove-Item -LiteralPath $install -Recurse -Force }; "
        f"$start = {_ps_literal(start_folder)}; "
        "if (Test-Path -LiteralPath $start) { Remove-Item -LiteralPath $start -Recurse -Force }; "
        f"$desktop = {_ps_literal(desktop_shortcut)}; "
        "if (Test-Path -LiteralPath $desktop) { Remove-Item -LiteralPath $desktop -Force }; "
        "$run = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'; "
        "if (Get-ItemProperty -LiteralPath $run -Name FunCorners -ErrorAction SilentlyContinue) { "
        "Remove-ItemProperty -LiteralPath $run -Name FunCorners }; "
        "$uninstall = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\FunCorners'; "
        "if (Test-Path -LiteralPath $uninstall) { Remove-Item -LiteralPath $uninstall -Force }"
    )


def _get_special_folder(csidl: int) -> Path:
    """Get Windows special folder using SHGetFolderPathW."""
    buf = ctypes.create_unicode_buffer(300)
    # CSIDL_PROGRAMS = 2 (Start Menu\Programs), CSIDL_DESKTOP = 0
    result = ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    if result != 0 or not buf.value:
        raise OSError("Windows could not locate the requested shortcut folder.")
    return Path(buf.value)


class InstallerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Fun Corners Setup")
        self.root.configure(bg=_BG)
        self.root.resizable(False, False)

        win_w, win_h = 560, 430
        sx = max(0, self.root.winfo_screenwidth() // 2 - win_w // 2)
        sy = max(0, self.root.winfo_screenheight() // 2 - win_h // 2)
        self.root.geometry(f"{win_w}x{win_h}+{sx}+{sy}")

        # Set window icon if available
        icon_path = Path(__file__).resolve().parent / "app_icon.ico"
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            bundled_ico = Path(sys._MEIPASS) / "app_icon.ico"
            if bundled_ico.exists():
                icon_path = bundled_ico
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception:
                pass

        # Installation target directory
        local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        self.install_dir = Path(local_app_data) / "Programs" / "FunCorners"

        # Checkbox variables
        self.var_desktop = tk.BooleanVar(value=True)
        self.var_startmenu = tk.BooleanVar(value=True)
        self.var_autostart = tk.BooleanVar(value=True)
        self.var_launch = tk.BooleanVar(value=True)

        self._build_ui()

    def _build_ui(self) -> None:
        # Header banner
        header = tk.Frame(self.root, bg="#1e1e24", height=80)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header, text="Install Fun Corners", font=("Segoe UI", 16, "bold"),
            bg="#1e1e24", fg="#ffffff",
        ).pack(anchor="w", padx=25, pady=(15, 2))

        tk.Label(
            header, text="Fast, lightweight modern hot-corners for your multi-monitor setup",
            font=("Segoe UI", 9), bg="#1e1e24", fg="#a0a0a5",
        ).pack(anchor="w", padx=25)

        # Content area
        content = tk.Frame(self.root, bg=_BG)
        content.pack(fill="both", expand=True, padx=25, pady=20)

        tk.Label(
            content, text=f"Fun Corners will be installed to:",
            font=("Segoe UI", 10), bg=_BG, fg="#333333",
        ).pack(anchor="w", pady=(0, 4))

        # Path display box
        path_box = tk.Frame(content, bg="#ffffff", bd=1, relief="solid")
        path_box.pack(fill="x", pady=(0, 18))
        tk.Label(
            path_box, text=str(self.install_dir), font=("Segoe UI", 9),
            bg="#ffffff", fg="#222222", padx=10, pady=6, anchor="w",
        ).pack(fill="x")

        tk.Label(
            content, text="Installation Options:",
            font=("Segoe UI", 10, "bold"), bg=_BG, fg="#222222",
        ).pack(anchor="w", pady=(0, 8))

        # Checkboxes
        opts_frame = tk.Frame(content, bg=_BG)
        opts_frame.pack(fill="x", padx=10)

        tk.Checkbutton(
            opts_frame, text="Create a Desktop shortcut",
            variable=self.var_desktop, font=("Segoe UI", 10), bg=_BG, anchor="w",
        ).pack(fill="x", pady=2)

        tk.Checkbutton(
            opts_frame, text="Add to Start Menu / Windows Applications list",
            variable=self.var_startmenu, font=("Segoe UI", 10), bg=_BG, anchor="w",
        ).pack(fill="x", pady=2)

        tk.Checkbutton(
            opts_frame, text="Start automatically when Windows starts",
            variable=self.var_autostart, font=("Segoe UI", 10), bg=_BG, anchor="w",
        ).pack(fill="x", pady=2)

        tk.Checkbutton(
            opts_frame, text="Launch Fun Corners immediately after installation",
            variable=self.var_launch, font=("Segoe UI", 10), bg=_BG, anchor="w",
        ).pack(fill="x", pady=2)

        # Bottom buttons
        bar = tk.Frame(self.root, bg=_BG)
        bar.pack(fill="x", padx=25, pady=(0, 22))

        btn_cancel = tk.Button(
            bar, text="Cancel", font=("Segoe UI", 9),
            bg="#ffffff", fg="#1a1a1a",
            activebackground="#f0f0f0", activeforeground="#1a1a1a",
            relief="solid", bd=1, highlightthickness=0,
            padx=18, pady=6, cursor="hand2", command=self.root.destroy,
        )
        btn_cancel.pack(side="right", padx=(10, 0))
        btn_cancel.bind("<Enter>", lambda e: btn_cancel.config(bg="#f4f4f4"))
        btn_cancel.bind("<Leave>", lambda e: btn_cancel.config(bg="#ffffff"))

        self.btn_install = tk.Button(
            bar, text="Install Now", font=("Segoe UI", 9, "bold"),
            bg=_ACCENT, fg="#ffffff", activebackground=_ACCENT_HI, activeforeground="#ffffff",
            relief="flat", bd=0, highlightthickness=0,
            padx=22, pady=7, cursor="hand2",
            command=self._do_install,
        )
        self.btn_install.pack(side="right", padx=(0, 4))
        self.btn_install.bind("<Enter>", lambda e: self.btn_install.config(bg=_ACCENT_HI))
        self.btn_install.bind("<Leave>", lambda e: self.btn_install.config(bg=_ACCENT))

    def _do_install(self) -> None:
        self.btn_install.config(state="disabled", text="Installing...")
        self.root.update()

        try:
            self._perform_installation()
            messagebox.showinfo(
                "Fun Corners Installed",
                "Fun Corners has been successfully installed!\n\nYou can access it anytime from the Start Menu, your desktop, or the system tray.",
            )
            self.root.destroy()
        except Exception as exc:
            messagebox.showerror("Installation Error", f"Failed to complete installation:\n{exc}")
            self.btn_install.config(state="normal", text="Install Now")

    def _perform_installation(self) -> None:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            bundle_dir = Path(sys._MEIPASS)
            dist_exe = bundle_dir / "dist" / "FunCorners.exe"
            icon_src = bundle_dir / "app_icon.ico"
        else:
            source_dir = Path(__file__).resolve().parent
            dist_exe = source_dir / "dist" / "FunCorners.exe"
            icon_src = source_dir / "app_icon.ico"

        if not dist_exe.exists():
            raise FileNotFoundError(f"Could not find {dist_exe}.")

        # 1. Create target directory
        self.install_dir.mkdir(parents=True, exist_ok=True)
        target_exe = self.install_dir / "FunCorners.exe"
        target_icon = self.install_dir / "app_icon.ico"

        # Copy executable and icon
        # Windows keeps a running executable open. Leave it intact and ask
        # the user to quit, rather than force-killing processes by name.
        try:
            shutil.copy2(dist_exe, target_exe)
        except PermissionError as exc:
            raise RuntimeError("Quit Fun Corners from its tray menu, then retry installation. "
                               "The existing executable has been left in place.") from exc
        if icon_src.exists():
            shutil.copy2(icon_src, target_icon)

        # 2. Start Menu shortcut & Programs folder
        start_menu_programs = _get_special_folder(2)  # CSIDL_PROGRAMS
        if not start_menu_programs.exists():
            # Fallback
            start_menu_programs = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"

        if self.var_startmenu.get():
            app_start_folder = start_menu_programs / "Fun Corners"
            app_start_folder.mkdir(parents=True, exist_ok=True)
            shortcut_path = app_start_folder / "Fun Corners.lnk"
            _create_shortcut(target_exe, shortcut_path, target_icon, "Hot Corners for multi-monitor Windows")

        # 3. Desktop shortcut
        if self.var_desktop.get():
            desktop_dir = _get_special_folder(0)  # CSIDL_DESKTOP
            if not desktop_dir.exists():
                desktop_dir = Path.home() / "Desktop"
            _create_shortcut(target_exe, desktop_dir / "Fun Corners.lnk", target_icon, "Hot Corners for multi-monitor Windows")

        # 4. Windows "Installed Apps" / Add-Remove Programs registry entry
        self._register_uninstall(target_exe, target_icon)

        # 5. Auto-start registry option
        from fun_corners.config import set_auto_start
        set_auto_start(self.var_autostart.get(), target_exe=target_exe)

        # 6. Launch if requested
        if self.var_launch.get():
            import subprocess
            subprocess.Popen([str(target_exe)], cwd=str(self.install_dir))

    def _register_uninstall(self, target_exe: Path, target_icon: Path) -> None:
        """Register the app in Windows Uninstall registry so it appears in Installed Apps."""
        uninstall_key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\FunCorners"
        script = _uninstall_script(
            self.install_dir,
            _get_special_folder(2) / "Fun Corners",
            _get_special_folder(0) / "Fun Corners.lnk",
        )
        uninst_cmd = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + _encoded_command(script)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, uninstall_key_path) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
            winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, APP_PUBLISHER)
            winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, str(self.install_dir))
            winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, str(target_icon if target_icon.exists() else target_exe))
            winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, uninst_cmd)



def main() -> None:
    root = tk.Tk()
    InstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
