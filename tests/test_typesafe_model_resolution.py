"""Offline regression tests for direct TypeSafe Jev release alias drift."""
import unittest

from hermes_switchyard.client import (
    DEFAULT_ENDPOINT,
    TYPESAFE_ENDPOINT,
    DecisionClient,
)

QUESTION = {"answer": {"type": "noul", "instructions": "Is this public example true?"}}


def client_for(requested, resolved, endpoint=TYPESAFE_ENDPOINT):
    return DecisionClient(
        api_key="synthetic-test-key",
        endpoint=endpoint,
        model=requested,
        transport=lambda payload: {
            "model": resolved,
            "answers": {"answer": {"noul": 0.9}},
        },
    )


def run(client):
    return client.decide("public synthetic example", QUESTION, public_or_sanitized_data_ack=True)


class TypeSafeModelResolutionTests(unittest.TestCase):
    def test_latest_accepts_jev_114_resolutions(self):
        for resolved in ("jev-1.14", "jev-1.14.0"):
            with self.subTest(resolved=resolved):
                self.assertEqual(run(client_for("jev-latest", resolved))["model"], resolved)

    def test_existing_jev_113_resolutions_remain_valid(self):
        for resolved in ("jev-1.13", "jev-1.13.0"):
            with self.subTest(resolved=resolved):
                self.assertEqual(run(client_for("jev-latest", resolved))["model"], resolved)

    def test_explicit_pin_does_not_allow_substitution(self):
        with self.assertRaisesRegex(ValueError, "different allowed alias"):
            run(client_for("jev-1.13.0", "jev-1.14.0"))

    def test_exact_jev_114_pin_is_supported(self):
        self.assertEqual(run(client_for("jev-1.14.0", "jev-1.14.0"))["model"], "jev-1.14.0")

    def test_unknown_versions_and_cross_endpoint_aliases_still_fail(self):
        for resolved in ("jev-1.14.1", "jev-1.15", "jev-1.15.0", "typesafe/jev-1.14.0"):
            with self.subTest(resolved=resolved):
                with self.assertRaisesRegex(ValueError, "outside the allowed aliases"):
                    run(client_for("jev-latest", resolved))
        with self.assertRaisesRegex(ValueError, "not an evidence-backed allowed alias"):
            client_for("jev-1.14.0", "jev-1.14.0", endpoint=DEFAULT_ENDPOINT)


if __name__ == "__main__":
    unittest.main()
