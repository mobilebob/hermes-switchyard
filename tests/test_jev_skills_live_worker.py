"""Offline input/admission contracts for the credential-free native worker."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1] / "evaluation/jev_skills_comparison"
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("stage_a_native_worker", HERE / "live_worker.py")
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
CASES = {c["id"]: c for c in json.loads((HERE / "cases.json").read_text())["cases"]}


def payload(case_id="routine-extract", arm="switchyard"):
    return {"arm": arm, "source_root": "/isolated-source", "catalog": copy.deepcopy(mod.CATALOG),
            "case": {k: CASES[case_id][k] for k in ("id", "task")}}


class NativeWorkerTests(unittest.TestCase):
    def test_public_case_and_catalog_are_exactly_bound(self):
        with patch.object(mod, "verify_source") as verify:
            mod.validate_input(payload())
            verify.assert_called_once_with(Path("/isolated-source"), "switchyard")

    def test_private_and_no_text_cases_are_rejected_before_loading_source(self):
        for case_id in ("private-message", "restricted-content"):
            for arm in ("switchyard", "jev-skills"):
                with self.subTest(case=case_id, arm=arm), patch.object(mod, "verify_source") as verify:
                    with self.assertRaises(ValueError):
                        mod.validate_input(payload(case_id, arm))
                    verify.assert_not_called()

    def test_task_substitution_and_privacy_label_injection_rejected(self):
        for field, value in (("task", "private replacement"), ("risk", "routine")):
            data = payload()
            data["case"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                mod.validate_input(data)

    def test_unknown_arm_and_modified_catalog_rejected(self):
        for kind in ("arm", "candidate", "description", "price", "current"):
            data = payload()
            if kind == "arm":
                data["arm"] = "other"
            elif kind == "candidate":
                data["catalog"]["models"][0]["model"] = "unapproved"
            elif kind == "description":
                data["catalog"]["models"][0]["description"] = "private note"
            elif kind == "price":
                data["catalog"]["models"][0]["cost"] = 999
            else:
                data["catalog"]["current"] = "different-provider:model"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                mod.validate_input(data)

    def test_source_identity_failure_is_not_ignored(self):
        with patch.object(mod, "verify_source", side_effect=ValueError("revision mismatch")):
            with self.assertRaises(ValueError):
                mod.validate_input(payload())

    def test_real_model_ids_are_shared_and_not_fixture_aliases(self):
        self.assertEqual({m["id"] for m in mod.CATALOG["models"]}, {"gpt-6-luna", "gpt-6.1-sol"})
        self.assertTrue(all(m["id"] == m["model"] for m in mod.CATALOG["models"]))
        self.assertEqual(mod.CATALOG["current"], "openai-codex:gpt-6.1-sol")
        self.assertIn("not Codex billing", mod.CATALOG["metadata_note"])


if __name__ == "__main__":
    unittest.main()
