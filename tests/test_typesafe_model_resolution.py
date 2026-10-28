"""Offline regression tests for direct TypeSafe Jev release alias drift."""
import unittest
from unittest import mock

from hermes_switchyard import client as client_module

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

    def test_latest_accepts_future_numeric_versions(self):
        for resolved in ("jev-1.14.1", "jev-1.15", "jev-1.15.0", "jev-2.0.0", "jev-2.1"):
            with self.subTest(resolved=resolved):
                self.assertEqual(run(client_for("jev-latest", resolved))["model"], resolved)

    def test_new_release_is_logged_once_without_private_request_data(self):
        with mock.patch.object(client_module, "_NEW_TYPESAFE_RELEASES_LOGGED", set()):
            with self.assertLogs(client_module.logger, level="WARNING") as observed:
                run(client_for("jev-latest", "jev-1.15.0"))
                run(client_for("jev-latest", "jev-1.15.0"))
                run(client_for("jev-latest", "jev-2.0.0"))
        self.assertEqual(len(observed.output), 2)
        self.assertIn("jev-1.15.0", observed.output[0])
        self.assertIn("jev-2.0.0", observed.output[1])
        self.assertNotIn("synthetic-test-key", " ".join(observed.output))

    def test_future_explicit_pin_is_not_implicitly_approved(self):
        with self.assertRaisesRegex(ValueError, "not an evidence-backed allowed alias"):
            client_for("jev-1.15.0", "jev-1.15.0")
        with self.assertRaisesRegex(ValueError, "not an evidence-backed allowed alias"):
            client_for("typesafe/jev-1.15.0", "typesafe/jev-1.15.0", endpoint=DEFAULT_ENDPOINT)

    def test_unknown_versions_and_cross_endpoint_aliases_still_fail(self):
        bad = (
            "jev-1.12.9", "jev-0.99", "jev-1.015", "jev-01.15",
            "jev-1.15.0-20261028", "jev-1.15.0-preview", "jev-1.15.0.1",
            "jev-1.15.0\\n", "Jev-1.15.0", "jev-10000.0",
            "jev-1.15.00001", "typesafe/jev-1.14.0",
            "unexpected/model", "jev-2.0.0 ",
            None, 1, [], {},
        )
        for resolved in bad:
            with self.subTest(resolved=resolved):
                with self.assertRaisesRegex(ValueError, "outside the allowed aliases"):
                    run(client_for("jev-latest", resolved))
        with self.assertRaisesRegex(ValueError, "not an evidence-backed allowed alias"):
            client_for("jev-1.14.0", "jev-1.14.0", endpoint=DEFAULT_ENDPOINT)


if __name__ == "__main__":
    unittest.main()
