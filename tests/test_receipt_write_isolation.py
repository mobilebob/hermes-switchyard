"""Receipt persistence must never break skill routing (issue #91).

On Windows, a receipt write error once escaped the pre_llm_call hook on
every turn, so no skill was recommended or loaded, and each failure left a
zero-byte ``.receipt-*.tmp`` file with an open handle. These tests inject
the same class of error on any platform. All fixtures are synthetic and
local; no test calls a hosted model.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hermes_switchyard import receipt_history, receipt_state
from hermes_switchyard import automatic
from hermes_switchyard.automatic import (
    AutomaticSkillRecommender,
    build_pre_llm_call_hook,
    build_routing_receipt,
)

_INJECTED = ModuleNotFoundError("No module named 'hermes_switchyard'")


def _skipped_receipt():
    return build_routing_receipt(
        {
            "status": "abstained",
            "selected": None,
            "source": "none",
            "abstention_reason": None,
            "hosted_skipped": "public_or_sanitized_data_ack_required",
            "hosted_attempted": False,
            "cache_hit": False,
            "local_score": 0.0,
        }
    )


def _open_descriptor_count() -> int | None:
    fd_dir = Path("/dev/fd")
    if not fd_dir.is_dir():
        return None
    return len(list(fd_dir.iterdir()))


_CANDIDATES = [
    {"name": "docker-management", "description": "Manage Docker containers and Compose services."},
    {"name": "network-printer-operations", "description": "Operate network printers and scanners."},
]
_PRIVATE_TEXT = "private-path-marker-91"


def _load_hook(loaded):
    def load_skill(name, task_id=None):
        loaded.append(name)
        return f"LOADED SKILL: {name}"

    hook = build_pre_llm_call_hook(
        configured_candidates=_CANDIDATES,
        routing_mode="local_only",
        consumer_mode="load",
        skill_loader=load_skill,
    )
    assert hook is not None
    return hook


class ReceiptWriteIsolationTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.home = Path(self._directory.name)
        patcher = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._directory.cleanup)
        path = receipt_state._receipt_state_file()
        self.assertIsNotNone(path)
        # Fail loudly instead of touching a real Hermes home.
        self.assertTrue(str(path).startswith(str(self.home)), path)
        self.receipt_path = path
        self.data_dir = path.parent

    def _temporaries(self) -> list[Path]:
        if not self.data_dir.exists():
            return []
        return sorted(self.data_dir.glob(".receipt-*.tmp"))

    def test_non_oserror_during_write_is_contained_and_cleaned_up(self):
        before = _open_descriptor_count()
        with mock.patch.object(receipt_state, "_apply_private_permissions", side_effect=_INJECTED):
            with self.assertLogs("hermes_switchyard.receipt_state", level="WARNING") as captured:
                stored = receipt_state.store_latest_receipt(_skipped_receipt())
        self.assertFalse(stored)
        self.assertIn("ModuleNotFoundError", "\n".join(captured.output))
        self.assertEqual(self._temporaries(), [])
        self.assertFalse(self.receipt_path.exists())
        if before is not None:
            self.assertEqual(_open_descriptor_count(), before)

    def test_hook_still_loads_the_skill_when_receipt_write_raises(self):
        loaded = []

        def load_skill(name, task_id=None):
            loaded.append(name)
            return f"LOADED SKILL: {name}"

        hook = build_pre_llm_call_hook(
            configured_candidates=[
                {"name": "docker-management", "description": "Manage Docker containers and Compose services."},
                {"name": "network-printer-operations", "description": "Operate network printers and scanners."},
            ],
            routing_mode="local_only",
            consumer_mode="load",
            skill_loader=load_skill,
        )
        assert hook is not None
        with mock.patch.object(receipt_state, "_apply_private_permissions", side_effect=_INJECTED):
            with self.assertLogs("hermes_switchyard.receipt_state", level="WARNING"):
                result = hook(
                    user_message="Diagnose an exiting Docker Compose container",
                    session_id="session-91",
                    turn_id="turn-1",
                )

        self.assertEqual(loaded, ["docker-management"])
        self.assertTrue(result["context"].startswith(
            '[Switchyard: skill "docker-management" already loaded for this turn]\n'
        ))
        self.assertIn('do not call skill_view(name="docker-management")', result["context"])
        self.assertTrue(result["context"].endswith("LOADED SKILL: docker-management"))
        self.assertEqual(result["metadata"]["skill_recommendation"]["status"], "loaded")
        self.assertEqual(self._temporaries(), [])

    def test_history_write_closes_descriptor_before_removing_failed_temporary(self):
        created: list[tuple[int, Path]] = []
        mkstemp = tempfile.mkstemp

        def tracked_mkstemp(*args, **kwargs):
            fd, path = mkstemp(*args, **kwargs)
            created.append((fd, Path(path)))
            return fd, path

        with (
            mock.patch.object(receipt_history.tempfile, "mkstemp", side_effect=tracked_mkstemp),
            mock.patch.object(receipt_state, "_apply_private_permissions", side_effect=_INJECTED),
        ):
            self.assertFalse(receipt_history.append_receipt_history(_skipped_receipt()))
        self.assertEqual(len(created), 1)
        fd, temporary = created[0]
        try:
            self.assertFalse(temporary.exists())
            with self.assertRaises(OSError):
                os.fstat(fd)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def test_successful_write_sweeps_only_stale_temporaries(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        stale = self.data_dir / ".receipt-stale000.tmp"
        recent = self.data_dir / ".receipt-recent00.tmp"
        unrelated = self.data_dir / "notes.tmp"
        for path in (stale, recent, unrelated):
            path.write_bytes(b"")
        old = time.time() - 2 * receipt_state.STALE_TEMPORARY_SECONDS
        os.utime(stale, (old, old))
        os.utime(unrelated, (old, old))

        self.assertTrue(receipt_state.store_latest_receipt(_skipped_receipt()))

        self.assertFalse(stale.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(self.receipt_path.exists())

    def _make_stale(self, *paths: Path) -> None:
        old = time.time() - 2 * receipt_state.STALE_TEMPORARY_SECONDS
        for path in paths:
            if path.is_symlink():
                os.utime(path, (old, old), follow_symlinks=False)
            else:
                os.utime(path, (old, old))

    def test_stale_matching_name_symlinks_and_directories_are_never_removed(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        outside = self.home / "outside"
        outside.mkdir()
        target = outside / ".receipt-target00.tmp"
        target.write_bytes(b"keep")
        link = self.data_dir / ".receipt-link0000.tmp"
        dangling = self.data_dir / ".receipt-dangle00.tmp"
        folder = self.data_dir / ".receipt-folder00.tmp"
        try:
            os.symlink(target, link)
            os.symlink(outside / "missing", dangling)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {type(exc).__name__}")
        folder.mkdir()
        self._make_stale(target, folder)
        try:
            self._make_stale(link, dangling)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink timestamps unavailable: {type(exc).__name__}")
        self.assertLess(link.lstat().st_mtime, time.time() - receipt_state.STALE_TEMPORARY_SECONDS)

        self.assertTrue(receipt_state.store_latest_receipt(_skipped_receipt()))

        self.assertTrue(link.is_symlink())
        self.assertTrue(dangling.is_symlink())
        self.assertTrue(folder.is_dir())
        self.assertEqual(target.read_bytes(), b"keep")

    def test_sweep_errors_of_any_type_keep_a_successful_result(self):
        targets = {
            "scandir": (receipt_state.os, "scandir"),
            "unlink": (receipt_state.os, "unlink"),
            "entry_check": (receipt_state, "_is_stale_temporary"),
        }
        for label, (owner, attribute) in targets.items():
            with self.subTest(step=label):
                self.data_dir.mkdir(parents=True, exist_ok=True)
                stale = self.data_dir / ".receipt-stale000.tmp"
                stale.write_bytes(b"")
                self._make_stale(stale)
                receipt = _skipped_receipt()
                with mock.patch.object(owner, attribute, side_effect=RuntimeError(_PRIVATE_TEXT)):
                    stored = receipt_state.store_latest_receipt(receipt)
                self.assertTrue(stored)
                self.assertTrue(self.receipt_path.exists())
                stale.unlink(missing_ok=True)

    def test_sweep_clock_error_keeps_a_successful_result(self):
        calls = []

        def clock():
            calls.append(1)
            raise RuntimeError(_PRIVATE_TEXT)

        receipt = _skipped_receipt()
        with mock.patch.object(receipt_state.time, "time", side_effect=clock):
            stored = receipt_state.store_latest_receipt(receipt)
        self.assertTrue(stored)
        self.assertTrue(calls)
        self.assertTrue(self.receipt_path.exists())

    def test_read_path_sweeps_stale_temporaries(self):
        self.assertTrue(receipt_state.store_latest_receipt(_skipped_receipt()))
        stale = self.data_dir / ".receipt-stale000.tmp"
        recent = self.data_dir / ".receipt-recent00.tmp"
        stale.write_bytes(b"")
        recent.write_bytes(b"")
        self._make_stale(stale)

        self.assertIsNotNone(receipt_state.read_latest_receipt())

        self.assertFalse(stale.exists())
        self.assertTrue(recent.exists())

    def test_descriptor_is_closed_when_any_write_step_fails(self):
        steps = {
            "fdopen": (receipt_state.os, "fdopen"),
            "fsync": (receipt_state.os, "fsync"),
            "json_dump": (receipt_state.json, "dump"),
            "replace": (receipt_state.os, "replace"),
        }
        for label, (owner, attribute) in steps.items():
            with self.subTest(step=label):
                receipt = _skipped_receipt()
                before = _open_descriptor_count()
                with mock.patch.object(owner, attribute, side_effect=RuntimeError(_PRIVATE_TEXT)):
                    with self.assertLogs("hermes_switchyard.receipt_state", level="WARNING") as captured:
                        stored = receipt_state.store_latest_receipt(receipt)
                self.assertFalse(stored)
                self.assertEqual(self._temporaries(), [])
                if before is not None:
                    self.assertEqual(_open_descriptor_count(), before)
                output = "\n".join(captured.output)
                self.assertIn("RuntimeError", output)
                self.assertNotIn(_PRIVATE_TEXT, output)
                self.assertNotIn(str(self.home), output)

    def test_hook_metadata_reports_persist_failure_and_still_loads(self):
        loaded = []
        hook = _load_hook(loaded)
        with mock.patch.object(receipt_state, "_apply_private_permissions", side_effect=_INJECTED):
            with self.assertLogs("hermes_switchyard.receipt_state", level="WARNING"):
                result = hook(
                    user_message="Diagnose an exiting Docker Compose container",
                    session_id="session-91",
                    turn_id="turn-flag",
                )
        self.assertEqual(loaded, ["docker-management"])
        self.assertIs(result["metadata"].get("receipt_persist_failed"), True)
        self.assertIs(hook.last_metadata.get("receipt_persist_failed"), True)
        self.assertIs(hook.last_routing_metadata.get("receipt_persist_failed"), True)
        self.assertNotIn(_PRIVATE_TEXT, json.dumps(result, sort_keys=True))

        # A later turn that saves its receipt clears the flag.
        second = hook(
            user_message="Diagnose an exiting Docker Compose container",
            session_id="session-91",
            turn_id="turn-ok",
        )
        self.assertNotIn("receipt_persist_failed", second["metadata"])
        self.assertNotIn("receipt_persist_failed", hook.last_metadata)
        self.assertTrue(self.receipt_path.exists())

    def test_hook_survives_when_the_store_function_itself_raises(self):
        loaded = []
        hook = _load_hook(loaded)
        cases = {
            "load": "Diagnose an exiting Docker Compose container",
            "override": "Use network-printer-operations to check the printer",
            "abstain": "zzqx unrelated words",
        }
        for label, message in cases.items():
            with self.subTest(case=label):
                with mock.patch.object(
                    receipt_state, "store_latest_receipt", side_effect=KeyError(_PRIVATE_TEXT)
                ):
                    with self.assertLogs("hermes_switchyard.automatic", level="WARNING") as captured:
                        result = hook(user_message=message, session_id="session-91", turn_id=label)
                self.assertIs(result["metadata"].get("receipt_persist_failed"), True)
                self.assertNotIn(_PRIVATE_TEXT, "\n".join(captured.output))
        self.assertEqual(loaded, ["docker-management"])

    def test_advisory_hook_reports_persist_failure(self):
        hook = build_pre_llm_call_hook(
            configured_candidates=_CANDIDATES, routing_mode="local_only", consumer_mode="advisory"
        )
        assert hook is not None
        with mock.patch.object(receipt_state, "store_latest_receipt", return_value=False):
            result = hook(user_message="Diagnose an exiting Docker Compose container")
        self.assertEqual(result["metadata"]["skill_recommendation"]["selected"], "docker-management")
        self.assertIs(result["metadata"].get("receipt_persist_failed"), True)

    def test_recommend_result_reports_persist_failure_without_caching_it(self):
        recommender = AutomaticSkillRecommender(
            configured_candidates=_CANDIDATES, routing_mode="local_only"
        )
        task = "Diagnose an exiting Docker Compose container"
        with mock.patch.object(receipt_state, "store_latest_receipt", side_effect=RuntimeError()):
            with self.assertLogs(automatic.logger.name, level="WARNING"):
                first = recommender.recommend(task)
        self.assertEqual(first["selected"], "docker-management")
        self.assertIs(first.get("receipt_persist_failed"), True)

        second = recommender.recommend(task)
        self.assertEqual(second["selected"], "docker-management")
        self.assertNotIn("receipt_persist_failed", second)


if __name__ == "__main__":
    unittest.main()
