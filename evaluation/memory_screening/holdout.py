"""Frozen offline protocol holdout. No served-Jev accuracy/latency claim."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[2]


class IndependentTransport:
    """A deliberately limited lexical fixture; it never receives gold labels/IDs."""
    def __init__(self):
        self.calls = []

    def __call__(self, payload):
        self.calls.append(copy.deepcopy(payload))
        answers = {}
        query = set(re.findall(r"[a-z]{4,}", payload["state"].get("recall_question", "").lower()))
        passages = list(payload["state"].get("memory_passages", {}).values())
        for name, question in payload["questions"].items():
            if question["type"] == "choice":
                keys = list(question["criteria"])
                selected = max(keys, key=lambda key: len(query & set(re.findall(r"[a-z]{4,}", str(question["criteria"][key]).lower()))))
                rest = [key for key in keys if key != selected]
                probabilities = {key: 0.01 / len(rest) for key in rest}
                probabilities[selected] = 0.99 if rest else 1.0
                answers[name] = {"choice": selected, "confidence": max(probabilities.values()), "probabilities": probabilities}
            else:
                index = int(name.split("_")[1])
                text = unicodedata.normalize("NFKC", passages[index]).lower()
                if name.endswith("_relevance"):
                    positive = bool(query & set(re.findall(r"[a-z]{4,}", text)))
                else:
                    # No tuning to gold labels: deliberately misses indirect intent
                    # and sometimes flags benign quotations. Report those limitations.
                    positive = bool(re.search(r"(?:ignore|override|disregard).{0,30}(?:instructions|rules)|<system>|system:", text))
                answers[name] = {"noul": 0.9 if positive else 0.1}
        return {"model": payload["model"], "answers": answers, "request_id": f"holdout-fixture-{len(self.calls)}", "usage": {}, "latency_ms": 1.0}


def evaluate():
    sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
    from hermes_switchyard import egress_redaction
    from hermes_switchyard.client import DecisionClient, EXPECTED_MODEL
    from hermes_switchyard.memory_screening import MemoryScreenShadow
    from hermes_switchyard.session_search_rerank import rerank_session_search
    from test_memory_screening import policy_for

    directory = Path(__file__).parent
    raw = (directory / "holdout.json").read_bytes()
    manifest = json.loads((directory / "holdout-manifest.json").read_text())
    assert hashlib.sha256(raw).hexdigest() == manifest["sha256"]
    cases = json.loads(raw)["cases"]
    assert len(cases) == manifest["cases"] and len({case["id"] for case in cases}) == len(cases)
    egress_redaction._reset_for_tests(lambda value: value, loaded=True)
    observations = []
    try:
        for case in cases:
            for enforce in (False, True):
                card = {"session_id": "holdout-primary", "title": "Public archive record", "snippet": case["text"] * case.get("repeat", 1), "match_message_id": "primary-message"}
                if "anchor" in case:
                    card.pop("match_message_id")
                    card["match_anchors"] = [{"message_id": "primary-message", "preview": case["anchor"]}]
                cards = [card, {"session_id": "holdout-other", "title": "Unrelated public index", "snippet": "A public list of assorted geographic locations.", "match_message_id": "other-message"}]
                policy = policy_for(case["query"], cards)
                from hermes_switchyard.memory_screening import digest
                policy["sources"][digest(card)] = case.get("source_class", "public")
                policy["queries"][digest(case["query"])] = case.get("query_class", "public")
                if "card_claim" in case:
                    card["source_class"] = case["card_claim"]
                private = case["group"] in {"privacy", "bounds"}
                base = IndependentTransport()
                expected = rerank_session_search(
                    query=case["query"], candidates=cards,
                    client=None if private else DecisionClient(api_key="fixture-key", model=EXPECTED_MODEL, transport=base),
                    retrieved_screen_enabled=enforce,
                )
                transport = IndependentTransport()
                owner = MemoryScreenShadow(mode="shadow", policy_factory=lambda: policy,
                                           identity_factory=lambda: {"provider": "fixture"},
                                           profile_factory=lambda: ("holdout-profile", "default"))
                original = copy.deepcopy(cards)
                result = rerank_session_search(
                    query=case["query"], candidates=cards,
                    client=DecisionClient(api_key="fixture-key", model=EXPECTED_MODEL, transport=transport),
                    retrieved_screen_enabled=enforce, memory_shadow=owner,
                    memory_context={"session_id": "holdout-session", "task_id": "holdout-task", "turn_id": case["id"]},
                )
                assert result == expected, (case["id"], enforce, result, expected)
                assert cards == original
                assert len(transport.calls) <= len(base.calls), (case["id"], enforce)
                if private:
                    assert not transport.calls, case["id"]
                receipt = owner.receipts[-1]
                selected = result.get("selected_session_id")
                retained = bool(case.get("fact") and selected == "holdout-primary" and case["fact"] in case["text"])
                observations.append({"id": case["id"], "group": case["group"], "local_enforcement": enforce,
                                     "output_identical": True, "cards_unchanged": True,
                                     "baseline_fixture_requests": len(base.calls), "shadow_fixture_requests": len(transport.calls),
                                     "required_fact_source_retained": retained,
                                     "gold_relevant": case["relevant"], "gold_injection": case["injection"],
                                     "receipt": receipt})
    finally:
        egress_redaction._reset_for_tests()
    plain = [row for row in observations if not row["local_enforcement"]]
    eligible = [row for row in plain if row["receipt"]["judged"]]
    diagnostic = []
    for row in eligible:
        diagnostic.append({"gold": row["gold_injection"],
                           "flagged": any(verdict["proposal"] == "warn" for verdict in row["receipt"]["verdicts"])})
    return {
        "evidence": "offline protocol holdout; deterministic lexical fixture, NOT served Jev",
        "holdout_sha256": manifest["sha256"], "cases": len(cases), "comparisons": len(observations),
        "output_mismatches": 0, "input_mutations": 0, "additional_fixture_requests": 0,
        "private_fixture_dispatches": sum(row["shadow_fixture_requests"] for row in observations if row["group"] in {"privacy", "bounds"}),
        "fixture_only_diagnostic": {
            "judged_cases": len(eligible), "attack_misses": sum(row["gold"] and not row["flagged"] for row in diagnostic),
            "benign_false_positives": sum(not row["gold"] and row["flagged"] for row in diagnostic),
            "not_a_jev_accuracy_measurement": True,
        },
        "observations": observations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import socket
    from unittest import mock
    with mock.patch.object(socket.socket, "connect", side_effect=AssertionError("No network")):
        result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "observations"}, indent=2))


if __name__ == "__main__":
    main()
