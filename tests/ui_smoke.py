"""Isolated manual UI fixture. System actions and registry writes are intercepted.

Run from workspace: python tests/ui_smoke.py. It exits after ten minutes or on
tray Quit. A transcript of intercepted action attempts stays inside tests/ui-runtime.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

workspace = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(workspace))
runtime = workspace / "tests" / "ui-runtime"
runtime.mkdir(exist_ok=True)
os.environ["APPDATA"] = str(runtime)

from fun_corners import actions, config, main
from fun_corners.monitor import find_corners, get_monitors

configuration = deepcopy(config.DEFAULTS)
detected = find_corners(get_monitors(), ignore_inner=True)
configuration["corners"] = {c.key: "show_desktop" if i == 0 else "disabled" for i, c in enumerate(detected)}
config.save(configuration)
startup = [False]
config.get_auto_start = lambda: startup[0]
config.set_auto_start = lambda enabled, **_kw: startup.__setitem__(0, enabled)

def intercept(action_id, options=None):
    with (runtime / "intercepted-actions.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"action": action_id, "options": options}) + "\n")

actions.execute = intercept
main._MUTEX_NAME += "_IsolatedUISmoke"
original_init = main.AppController.__init__

def init(self, root, engine):
    original_init(self, root, engine)
    root.after(600_000, lambda: self.commands.put("quit"))

main.AppController.__init__ = init
sys.argv = ["ui_smoke.py", "--preview"]
main.main()
