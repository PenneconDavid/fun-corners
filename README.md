# Fun Corners

A Windows 10/11 hot-corners utility with a visual monitor map, configurable dwell timing, and a system tray. Move to an assigned screen corner and hold briefly to run its action.

## Run

Use `dist\FunCorners.exe` as a portable app, or `dist\FunCorners-Setup.exe` to install to `%LOCALAPPDATA%\Programs\FunCorners`. Quit an existing instance from its tray menu before upgrading. The installer can add shortcuts and Windows startup.

From source (Python 3.10+):

```powershell
python -m pip install -r requirements.txt
python -m fun_corners
```

Double-clicking `run.pyw` also works. Source-mode Windows startup references this launcher by absolute path, so keep the source folder in place while startup is on.

## Configure and test safely

Open Settings from the tray. Select a corner in the map or list, choose an action, and select **Save & apply**. Apply keeps Settings open. Ctrl+S saves; Esc closes.

Enable **Safe preview** before trying an assignment. Preview uses **saved** settings: save a new choice before testing it. The map highlights the active corner, dwell progress fills, and the status says what would run, without executing it. Closing Settings ends preview and returns to live operation. `python -m fun_corners --preview` (or `FunCorners.exe --preview`) opens directly in preview. An action that already started before pause/preview/quit may finish; new triggers are blocked.

The window resizes and scrolls. In a short window, the map hides to leave room for the corner editor; enlarge the window to restore it. Monitor changes preserve your opening edits and show a notice to save and reopen Settings for the new layout.

## Actions and controls

Built-in actions: Sleep Displays, Screensaver, Show Desktop, Task View, Screen Snip, Quick Settings, Search, File Explorer, Start Menu, Mute, Play/Pause, and Lock.

- **Custom Keyboard Shortcut:** use a letter, digit, F1-F24, or navigation key, with optional Ctrl/Shift/Alt/Win modifiers; for example `Ctrl+Alt+K`. Held trigger modifiers are temporarily neutralized and restored around injected shortcuts.
- **Launch Application:** choose an existing `.exe`. It runs directly, without a shell or command-line arguments.
- **Per-corner timing:** leave dwell blank and required key on Inherit to use the defaults from Behavior. Override either for more deliberate activation.
- **Timed pause:** tray or Behavior offers 5, 15, and 60 minutes, plus Resume now. A manual Disabled state remains disabled when a temporary pause expires.
- **Excluded apps:** list executable basenames, one per line, such as `game.exe`, or use Add application. Exclusions apply while that executable is foreground. Windows may prevent identifying an elevated/protected process.
- **Fullscreen suppression:** applies globally when a foreground non-maximized window covers a monitor. Ordinary maximized windows remain enabled.
- **Re-arm:** after an action, leave its corner beyond the deadband and wait for the cooldown. This avoids repeat firing while the cursor stays parked.

## Multiple monitors and saved layouts

The engine finds convex outer corners and optional inner step corners across adjacent, stacked, staggered, and negative-coordinate monitor layouts. Equal-height adjacent screens expose four outer corners; staggered screens expose more.

Assignments follow a Windows monitor device-interface identity plus corner position. Existing coordinate assignments migrate in memory, then persist on the next save. Each saved topology retains its own actions, shortcut/application parameters and per-corner overrides. Configure and save each docked/undocked layout once; returning to it restores its profile automatically. A new topology falls back to monitor bindings. Changing GPU/port can change identity; adapter-name fallback is best effort.

## Settings and diagnostics

Preferences: `%APPDATA%\FunCorners\config.json`. Invalid JSON/types/ranges load safe defaults with a visible warning; the original remains untouched until a repair save backs it up as `config.before-repair-*.json`. Saves replace the file atomically, and failures remain visible and retryable. Bounded logs live beside the configuration.

Uninstall removes the installed executable, its shortcuts and registry entries, and preserves APPDATA preferences. The uninstall script targets the exact installed executable rather than stopping every process with the same name.

## Build and verify

```powershell
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -v
.\build.bat
```

The build script checks prerequisites without installing packages. It creates `dist\FunCorners.exe` and `dist\FunCorners-Setup.exe`.

Tests intercept keyboard, display, registry, launcher and uninstall effects. `python tests\ui_smoke.py` opens an isolated real UI fixture with intercepted actions and registry writes; its preferences/logs stay in `tests\ui-runtime`, and it exits after ten minutes or tray Quit. It does not use your normal app preferences.

See [IMPLEMENTATION.md](IMPLEMENTATION.md) for the plan, verification evidence, known limits and source-baseline location. Live lock/sleep behavior, physical monitor hotplug, install/uninstall, and protected-process exclusions need manual confirmation.

## License

Licensed under the [MIT License](LICENSE). Copyright (c) 2026 David Seibold.
