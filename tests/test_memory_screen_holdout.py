"""Frozen independent labels; lexical responses are not Jev-quality evidence."""
from __future__ import annotations

import socket
from unittest import mock

from evaluation.memory_screening.holdout import evaluate
from test_support import HermesHomeTestCase


class MemoryHoldoutTests(HermesHomeTestCase):
    def test_frozen_holdout_preserves_output_privacy_and_physical_request_budget(self):
        with mock.patch.object(socket.socket, "connect", side_effect=AssertionError("No network")):
            result = evaluate()
        self.assertEqual(result["cases"], 32)
        self.assertEqual(result["comparisons"], 64)
        self.assertEqual(result["output_mismatches"], 0)
        self.assertEqual(result["input_mutations"], 0)
        self.assertEqual(result["additional_fixture_requests"], 0)
        self.assertEqual(result["private_fixture_dispatches"], 0)
        self.assertTrue(result["fixture_only_diagnostic"]["not_a_jev_accuracy_measurement"])
        # The deliberately limited fixture is not gold-driven or called perfect.
        self.assertGreater(result["fixture_only_diagnostic"]["attack_misses"], 0)
        self.assertGreater(result["fixture_only_diagnostic"]["benign_false_positives"], 0)
