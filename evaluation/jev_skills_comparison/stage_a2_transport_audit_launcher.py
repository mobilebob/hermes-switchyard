"""Launch the read-only Stage A2 transport audit through Hermes' actual interpreter."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    entry = "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"
    audit = Path(__file__).with_name("stage_a2_transport_audit.py").resolve()
    try:
        cmd = json.loads(subprocess.check_output(
            ["hermes", "--print-runtime-command"], text=True, timeout=20))
        if (not isinstance(cmd, list) or len(cmd) < 3
                or not all(isinstance(arg, str) for arg in cmd)
                or cmd[-1].count(entry) != 1):
            raise ValueError("Unknown Hermes Python command shape")
        code = cmd[-1].replace(entry, f"runpy.run_path({str(audit)!r}, run_name='__main__')")
        # Hermes PM selects its committed dependency environment through HERMES_HOME.
        # A fresh temporary home has no installation record and fails *before* the
        # read-only audit can run. Keep the caller's home (or Hermes' default) so
        # the installed interpreter can activate. The audit itself only imports
        # source and uses MockTransport; no agent session or provider call occurs.
        env = os.environ.copy()
        env.update({"HERMES_DISABLE_LAZY_INSTALLS": "1",
                    "PYTHONDONTWRITEBYTECODE": "1"})
        completed = subprocess.run([*cmd[:-1], code], env=env, timeout=60, check=False)
        return completed.returncode
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Offline Hermes transport audit unavailable: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
