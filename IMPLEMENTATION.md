# Fun Corners implementation and verification

Poteto mode: active; Feature playbook. Scope: local source, tests, documentation,
and verified local builds. No install, live registry changes, publication, commit,
push, or deployment. Pre-change sources are retained in `review-baseline/` because
this workspace has no Git repository.

## Plan and acceptance criteria

1. Native reliability: pointer-safe Win32 calls; modifier-neutral shortcuts with
   key-state restoration; correct literal uninstall paths; stable source launcher.
2. Configuration: validate types and bounds, retain malformed originals, atomic
   saves, monitor identity bindings with legacy migration and layout snapshots.
3. Lifecycle: one main-thread Tk loop handles settings, display changes and quit;
   quitting closes settings and stops the engine. Errors remain visible/retryable.
4. Product: saved-settings preview never dispatches an action; display current
   corner/dwell/status; timed pause automatically resumes; exclusions match the
   foreground executable; per-corner tuning inherits global defaults; custom
   shortcuts and executable launch use explicit validated input.
5. Verification: deterministic unit tests mock system effects; inspect real Windows
   settings at normal/small sizes; independent review; build both executables when
   local tools permit. Actual lock/sleep, hotplug and uninstall are not automated.

## Ownership and integration

Independent workers own actions/installer/build, config/monitor, and settings UI.
The primary worker owns engine/lifecycle/tray/integration. Shared contracts are
defined before implementation: coordinate keys retained; stable binding aliases;
layout-aware configuration helpers; immutable engine status/layout snapshots;
non-blocking settings Toplevel hosted by one hidden Tk root.

## Decision trail

- Prefer a persistent Tk root and queued tray commands over cross-thread Tk calls.
- Prefer native ttk layout improvements over adding a UI framework.
- Preview uses saved settings explicitly, so unsaved changes cannot silently become
  active actions. Apply keeps the window open for testing.
- Legacy coordinate assignments migrate as a batch before stable bindings exist.
- Unit tests do not establish live keyboard, display power or uninstall behavior.

## Verification and final state

Implementation complete locally. All six review fixes, the five polish areas
(preview, monitor/layout persistence, status, resilient UI/config, regression tests),
and four feature expansions are implemented. README documents their behavior.

- `python -m unittest discover -s tests -q`: **70 tests passed**. Covers native ABI,
  modifier restoration and partial-input cleanup, malformed settings and atomic
  failures, legacy migration, per-layout actions AND parameters, monitor geometry,
  preview with no dispatch, pause/resume, app/fullscreen suppression, failed actions,
  nonblocking state access during slow actions, hotplug-save snapshots, save-error
  recovery, compact sizing, quit with settings open, and tray-start failure.
- Independent reviewers found and verified fixes for cross-layout parameter mixing,
  holding the state lock during native calls, and partial configuration application
  on monitor enumeration failure. Final focused re-review found no blocker.
- Real Windows UI inspected through Computer Use at 860x800 and 580x800 client
  sizes, then about 580x490. The first short-window check exposed an invisible
  editor; after repair, the map hides, editor scrolls, and Save stays reachable.
  Save & apply kept Settings open with safe preview checked. Behavior settings and
  scrolling were inspected. Screenshots are in this chat's tool evidence.
- UI fixture intercepted actions/registry and used `tests/ui-runtime`. Normal user
  preferences were not changed. The initial sandbox prevented a tray API call;
  the isolated UI fixture was then verified outside the sandbox. That failure also
  prompted explicit app-exit handling if the tray cannot start.
- `cmd /c build.bat`: **exit 0**, both executables rebuilt with PyInstaller 6.22.3.
  The standalone icon was found in its archive; the setup archive's embedded
  FunCorners.exe was compared byte-for-byte with the current standalone build.
- Rebuilt portable EXE started successfully in `--preview` with an empty isolated
  `tests/portable-runtime` configuration. Its Settings UI showed both real monitors
  and six usable corners, with preview checked. Test instances were stopped.
- The generated uninstall PowerShell passed the Windows parser without execution.

Artifacts: `dist/FunCorners.exe`, `dist/FunCorners-Setup.exe`, and
`dist/SHA256SUMS.txt`. Original source and previous EXEs remain in
`review-baseline/`; `review-changes.diff` compares the changed existing source files.
New tests and notes are separate files. No Git metadata was created during the
implementation pass; repository publication is a subsequent, separately requested step.

## Remaining manual checks

Real display power, lock, input injection into ordinary/elevated apps, physical
monitor unplug/replug, Windows login startup, install/upgrade/uninstall, and elevated
foreground-process identification were not exercised. Automated tests mocked those
effects; build success does not establish their end-to-end behavior. A native action
already reserved before pause/preview/quit may finish, but does not block UI state
reads and completion cannot overwrite newer state. Display identity may change
when GPU/port changes; adapter fallback is best effort. Layout profiles restore
automatically after each topology has been configured and saved once.

The implementation pass did not commit, push, install, write live registry entries,
publish, or deploy. The user subsequently requested GitHub repository creation and
an initial commit/push. Generated binaries, local review history and test profiles
remain on disk and are excluded from source control.
