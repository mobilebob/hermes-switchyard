"""CLI smoke tests for the Phase-1 provenance and usage reports."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from unittest import mock

import hermes_switchyard
from hermes_switchyard import runtime_provenance, usage_ledger


class PhaseOneCLITests(unittest.TestCase):
    def setUp(self):
        self.parser = argparse.ArgumentParser()
        hermes_switchyard._setup_cli(self.parser)

    def test_runtime_verify_is_read_only_report_dispatch(self):
        args = self.parser.parse_args(["runtime-verify", "--days", "14", "--json"])
        data = {"status": "unverified", "gateway_runtime_verified": False}
        out = io.StringIO()
        with mock.patch.object(runtime_provenance, "report", return_value=data) as report:
            with contextlib.redirect_stdout(out):
                result = hermes_switchyard._cli_handler(args)
        self.assertEqual(result, 0)
        report.assert_called_once_with(days=14)
        self.assertEqual(json.loads(out.getvalue()), data)

    def test_jev_usage_dispatch_reports_missing_capture_explicitly(self):
        args = self.parser.parse_args(["jev-usage", "--days", "30", "--json"])
        data = {"days": 30, "status": "unavailable", "reason": "no_ledger"}
        out = io.StringIO()
        with mock.patch.object(usage_ledger, "report", return_value=data) as report:
            with contextlib.redirect_stdout(out):
                result = hermes_switchyard._cli_handler(args)
        self.assertEqual(result, 1)
        report.assert_called_once_with(days=30)
        self.assertEqual(json.loads(out.getvalue())["reason"], "no_ledger")

    def test_invalid_days_are_rejected_before_io(self):
        for command in ("runtime-verify", "jev-usage"):
            args = self.parser.parse_args([command, "--days", "0"])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(hermes_switchyard._cli_handler(args), 2)


if __name__ == "__main__":
    unittest.main()
