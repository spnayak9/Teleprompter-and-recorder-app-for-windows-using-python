import sys
from pathlib import Path

files = [
    "teleprompter_app/app.py",
    "teleprompter_app/ui/main_window.py",
    "teleprompter_app/ui/settings_panel.py",
    "teleprompter_app/ui_config.py",
    "teleprompter_app/ui_main.py",
    "teleprompter_app/recorder.py",
    "teleprompter_app/config_manager.py",
    "teleprompter_app/preview.py",
]

errs = []
for f in files:
    p = Path(f)
    if not p.exists():
        print("MISSING:", f)
        errs.append((f, "missing"))
        continue
    try:
        src = p.read_text(encoding="utf-8")
        compile(src, f, "exec")
    except Exception as e:
        print("ERROR", f, e)
        errs.append((f, str(e)))

if errs:
    print("Compilation finished: ERRORS", len(errs))
    sys.exit(1)
else:
    print("Compilation finished: OK")
    sys.exit(0)
