"""The read-only audit must retain Hermes' committed dependency home."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "evaluation/jev_skills_comparison/stage_a2_transport_audit_launcher.py"
spec = importlib.util.spec_from_file_location("stage_a2_transport_audit_launcher_under_test", MODULE)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
ENTRY = "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"
CMD = ["/opt/hermes/bin/python", "-I", f"import runpy; {ENTRY}"]


class LauncherTests(unittest.TestCase):
    def test_preserves_explicit_install_home_and_has_no_network_dispatch(self):
        with patch.object(launcher.subprocess, "check_output", return_value=json.dumps(CMD)) as read, \
             patch.object(launcher.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run, \
             patch.dict(launcher.os.environ, {"HERMES_HOME": "/home/hermes/.hermes"}):
            self.assertEqual(launcher.main(), 0)
        read.assert_called_once_with(["hermes", "--print-runtime-command"], text=True, timeout=20)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], CMD[:2])
        self.assertIn("stage_a2_transport_audit.py", argv[-1])
        self.assertNotIn(ENTRY, argv[-1])
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["HERMES_HOME"], "/home/hermes/.hermes")
        self.assertEqual(env["HERMES_DISABLE_LAZY_INSTALLS"], "1")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(run.call_args.kwargs["timeout"], 60)
        self.assertIs(run.call_args.kwargs["check"], False)

    def test_does_not_invent_home_when_caller_has_none(self):
        with patch.object(launcher.subprocess, "check_output", return_value=json.dumps(CMD)), \
             patch.object(launcher.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run, \
             patch.dict(launcher.os.environ, {}, clear=True):
            self.assertEqual(launcher.main(), 0)
        self.assertNotIn("HERMES_HOME", run.call_args.kwargs["env"])

    def test_malformed_runtime_command_fails_closed(self):
        with patch.object(launcher.subprocess, "check_output", return_value=json.dumps(["python", "-I", "unknown"])), \
             patch.object(launcher.subprocess, "run") as run:
            self.assertEqual(launcher.main(), 2)
        run.assert_not_called()

    def test_child_failure_propagates_without_restart_or_repair(self):
        with patch.object(launcher.subprocess, "check_output", return_value=json.dumps(CMD)), \
             patch.object(launcher.subprocess, "run", return_value=SimpleNamespace(returncode=2)) as run:
            self.assertEqual(launcher.main(), 2)
        self.assertNotIn("repair", " ".join(run.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()
