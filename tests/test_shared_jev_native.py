"""Native Hermes registration/dispatch of an installed release archive, offline."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.build_release import build_release
from test_release import _fixture_repo

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = (
    "default-off", "shared", "cancel", "overlap", "concurrent", "retry",
    "outage", "stale", "route-before", "route-inflight", "route-after",
    "large", "deadline", "stage-two", "stage-two-failure", "unload", "no-skill", "private", "consume-once",
    "stage-two-after-shared", "failed-stage-two-after-shared", "concurrent-inflight",
    "shadow-success", "shadow-off", "shadow-apply", "shadow-pin", "shadow-unknown-pin",
    "shadow-provider", "shadow-account", "shadow-identity", "shadow-cancel",
    "shadow-cancel-inflight", "shadow-stale", "shadow-unavailable", "shadow-private",
    "shadow-observer-failure",
)


@unittest.skipUnless(importlib.util.find_spec("hermes_cli"), "native Hermes runtime required")
class SharedJevNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix="shared-jev-artifact-")
        cls.addClassCleanup(cls.fixture.cleanup)
        base = Path(cls.fixture.name)
        source, sha = _fixture_repo(base)
        cls.archive = build_release(source, base / "dist", sha)

    def exercise(self, scenario):
        with tempfile.TemporaryDirectory(prefix="shared-jev-native-") as directory:
            base = Path(directory)
            home = base / "hermes"
            home.mkdir()
            env = {
                "PATH": os.environ.get("PATH", ""), "HOME": str(base),
                "HERMES_HOME": str(home), "HERMES_PROFILE": "default",
                "HERMES_SHARED_AUTH_DIR": str(base / "shared"),
                "HERMES_BUNDLED_PLUGINS": str(base / "empty-bundled"),
                "HERMES_ENABLE_PROJECT_PLUGINS": "0",
                "PYTHONNOUSERSITE": "1", "TMPDIR": tempfile.gettempdir(),
                "LANG": "C.UTF-8",
            }
            commands = []
            # A second fresh interpreter checks installed defaults after restart.
            for _ in range(2 if scenario in {"default-off", "shadow-off"} else 1):
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "tests/shared_jev_native_worker.py"),
                     str(self.archive), scenario],
                    cwd=base, env=env, text=True, capture_output=True, timeout=25,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                result = json.loads(completed.stdout.strip().splitlines()[-1])
                self.assertEqual(result["scenario"], scenario)
                self.assertEqual(result["installed_module"], "plugins/hermes-switchyard/hermes_switchyard/__init__.py")
                self.assertEqual(result["network"], "socket.connect blocked")
                commands.append(result)
                print("NATIVE_RECEIPT " + json.dumps(result), flush=True)
            if scenario == "default-off":
                self.assertTrue(all(value["default_off"] for value in commands))


def _case(scenario):
    def test(self):
        self.exercise(scenario)
    return test


for _scenario in SCENARIOS:
    setattr(SharedJevNativeTests, "test_" + _scenario.replace("-", "_"), _case(_scenario))


if __name__ == "__main__":
    unittest.main()
