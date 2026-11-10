"""Offline request-joining contract for Switchyard's existing Jev transfer pilot.

No credentials, Jev client initialization, gateway, or network are involved.
Mocking the base DecisionClient isolates the *question construction* and
preservation contract; it does NOT test skill_view loading or live accuracy.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "hermes_switchyard._contract_combined_pilot",
    ROOT / "evaluation/turn_consolidation/pilot.py",
)
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


class CombinedDecisionContractTests(unittest.TestCase):
    TASK = "Use the public CSV skill to explain a synthetic example."
    KEY = ("public-session", "public-task", "public-turn")
    SKILL_QUESTION = {
        "skill": {
            "type": "choice",
            "instructions": "Choose a public skill or none",
            "criteria": {"none": "No skill", "csv-skill": "CSV operations"},
        }
    }

    def setUp(self):
        self.broker = pilot.Broker()
        self.scope = {
            "task": self.TASK,
            "key": self.KEY,
            "broker": self.broker,
        }
        self.client = pilot.JointClient.__new__(pilot.JointClient)
        self.calls = []
        self.effort_calls = []

    def fake_effort_parts(self, task, cap):
        self.effort_calls.append((task, cap))
        return (
            {"current_request": task, "public_shape": "synthetic"},
            {
                "reasoning_effort": {
                    "type": "choice",
                    "instructions": "Select effort for current_request",
                    "criteria": {"low": "Low", "high": "High"},
                },
                "stakes": {
                    "type": "noul",
                    "instructions": "Is current_request high risk?",
                    "criteria": {"true": "Yes", "false": "No"},
                },
            },
        )

    def fake_provider_decide(self, _client, state, questions, **kwargs):
        self.calls.append((copy.deepcopy(state), copy.deepcopy(questions)))
        return {
            "request_id": "fixture-request-id",
            "answers": {
                key: ({"choice": "csv-skill"} if key == "skill"
                      else {"choice": "low"})
                for key in questions
            },
        }

    def run_decide(self, *, state=None, questions=None, scope=True):
        state = {"task": self.TASK, "stage": 1} if state is None else state
        questions = copy.deepcopy(
            self.SKILL_QUESTION if questions is None else questions
        )
        token = pilot.PRE.set(self.scope if scope else None)
        try:
            with mock.patch.object(
                pilot.DecisionClient, "decide",
                autospec=True,
                side_effect=self.fake_provider_decide,
            ), mock.patch.object(
                pilot, "effort_parts", side_effect=self.fake_effort_parts
            ):
                return self.client.decide(state, questions)
        finally:
            pilot.PRE.reset(token)

    def test_first_stage_combines_one_provider_call(self):
        result = self.run_decide()
        self.assertEqual(len(self.calls), 1)
        state, questions = self.calls[0]
        self.assertEqual(state["task"], self.TASK)
        self.assertEqual(
            set(questions),
            {"skill", "reasoning_effort_medium",
             "reasoning_effort_high", "stakes"},
        )
        self.assertEqual(
            self.effort_calls,
            [(self.TASK, "medium"), (self.TASK, "high")],
        )
        self.assertNotIn("current_request", questions["stakes"]["instructions"])

    def test_skill_consumer_receives_only_original_answers(self):
        result = self.run_decide()
        self.assertEqual(
            result["answers"], {"skill": {"choice": "csv-skill"}}
        )
        self.assertEqual(result["request_id"], "fixture-request-id")
        self.assertEqual(
            set(self.broker.records[self.KEY]["result"]["answers"]),
            {"skill", "reasoning_effort_medium",
             "reasoning_effort_high", "stakes"},
        )

    def test_original_skill_questions_are_unchanged(self):
        questions = copy.deepcopy(self.SKILL_QUESTION)
        state = {"task": self.TASK, "stage": 1}
        self.run_decide(state=state, questions=questions)
        self.assertEqual(questions, self.SKILL_QUESTION)
        self.assertEqual(state, {"task": self.TASK, "stage": 1})

    def test_second_stage_does_not_merge(self):
        result = self.run_decide(
            state={"task": self.TASK, "stage": 2}
        )
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], self.SKILL_QUESTION)
        self.assertEqual(self.effort_calls, [])
        self.assertEqual(self.broker.records, {})
        self.assertEqual(set(result["answers"]), {"skill"})

    def test_task_mismatch_does_not_merge_or_cache(self):
        self.run_decide(state={"task": "A different public task"})
        self.assertEqual(self.calls[0][1], self.SKILL_QUESTION)
        self.assertEqual(self.effort_calls, [])
        self.assertEqual(self.broker.records, {})

    def test_non_skill_question_does_not_merge(self):
        other = {"classification": {"type": "noul"}}
        self.run_decide(questions=other)
        self.assertEqual(self.calls[0][1], other)
        self.assertEqual(self.effort_calls, [])
        self.assertEqual(self.broker.records, {})

    def test_without_pre_hook_scope_no_combination(self):
        self.run_decide(scope=False)
        self.assertEqual(self.calls[0][1], self.SKILL_QUESTION)
        self.assertEqual(self.effort_calls, [])
        self.assertEqual(self.broker.records, {})


if __name__ == "__main__":
    unittest.main()
