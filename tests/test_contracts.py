"""Proves this repo's audit records and eval test cases conform to the shared
JSON schemas in contracts/, and that the schemas reject malformed data.

No network or LLM calls: audit records are produced by calling
audit_log.log_transaction() directly against a temp file.

Run: python -m unittest tests.test_contracts   (from the repo root)
"""

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "evals"))

from audit_log import log_transaction, verify_log  # noqa: E402
from contracts.verdicts import CANONICAL_VALUES, normalize_verdict  # noqa: E402
from test_cases import TEST_CASES  # noqa: E402


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "contracts" / f"{name}.v1.schema.json").read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _stage(valid=True, result=None):
    return {
        "boundary_valid": valid,
        "boundary_reason": None if valid else "Rejected at the boundary.",
        "subagent_result": result,
    }


SANCTIONS_CLEAR = {"name": "A B", "verdict": "CLEAR", "confidence": "high", "explanation": "x"}
SANCTIONS_MATCH = {"name": "A B", "verdict": "MATCH", "confidence": "high", "explanation": "x"}
ENRICH_LOW = {"customer_id": "CUST-1001", "risk_score": "low", "found": True,
              "confidence": "high", "explanation": "x"}

# (decision, routing, review_reasons, sanctions stage, enrichment stage)
SCENARIOS = {
    "clear": ("clear", "auto_decided", [], _stage(result=SANCTIONS_CLEAR), _stage(result=ENRICH_LOW)),
    "block": ("block", "auto_decided", [], _stage(result=SANCTIONS_MATCH), _stage(result=ENRICH_LOW)),
    "review_with_reasons": (
        "review", "human_review_required",
        [{"rule": "elevated_risk_tier", "detail": "risk tier is medium"}],
        _stage(result=SANCTIONS_CLEAR), _stage(result=ENRICH_LOW),
    ),
    "boundary_rejected": (
        "review", "human_review_required",
        [{"rule": "validation_flagged", "detail": "sender_name failed input validation"}],
        _stage(valid=False), _stage(result=ENRICH_LOW),
    ),
}


class AuditEventConformance(unittest.TestCase):
    def setUp(self):
        self.validator = _validator("audit-event")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "transactions.jsonl"

    def _write_all(self):
        for name, (decision, routing, reasons, sanctions, enrichment) in SCENARIOS.items():
            log_transaction(
                sender_name="Sofia Andersen", customer_id="CUST-1001", amount=1200.0,
                sanctions_info=sanctions, enrichment_info=enrichment,
                decision=decision, confidence="high", routing=routing,
                review_reasons=reasons, path=self.path,
            )

    def test_every_written_record_conforms(self):
        self._write_all()
        lines = self.path.read_text("utf-8").splitlines()
        self.assertEqual(len(lines), len(SCENARIOS))
        for line in lines:
            record = json.loads(line)
            self.validator.validate(record)
            datetime.fromisoformat(record["timestamp"])  # backstop to the date-time FormatChecker
        self.assertTrue(verify_log(self.path)["valid"])

    def _one_record(self) -> dict:
        self._write_all()
        return json.loads(self.path.read_text("utf-8").splitlines()[0])

    def test_rejects_malformed_records(self):
        base = self._one_record()
        mutations = {
            "missing record_hash": lambda r: r.pop("record_hash"),
            "unknown decision": lambda r: r.update(decision="approve"),
            "extra top-level field": lambda r: r.update(extra=1),
            "string review reason": lambda r: r.update(review_reasons=["elevated_risk_tier"]),
            "bad hash": lambda r: r.update(record_hash="xyz"),
            "bad timestamp": lambda r: r.update(timestamp="yesterday"),
            "extra key in stage": lambda r: r["enrichment"].update(extra=1),
            "routing contradicts decision": lambda r: r.update(routing="human_review_required"),
            "unknown sanctions verdict": lambda r: r["sanctions_screening"]["subagent_result"].update(verdict="maybe"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                bad = copy.deepcopy(base)
                mutate(bad)
                self.assertFalse(self.validator.is_valid(bad))


class EvalTestCaseConformance(unittest.TestCase):
    def setUp(self):
        self.validator = _validator("eval-test-case")

    def test_every_test_case_conforms(self):
        for case in TEST_CASES:
            with self.subTest(case["id"]):
                self.validator.validate(case)

    def test_ids_unique(self):
        ids = [c["id"] for c in TEST_CASES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_rejects_malformed_cases(self):
        base = TEST_CASES[0]
        for label, change in {
            "bad decision": {"expected_decision": "approve"},
            "bad category": {"category": "other"},
            "string amount": {"amount": "12"},
            "extra field": {"surprise": True},
        }.items():
            with self.subTest(label):
                self.assertFalse(self.validator.is_valid({**base, **change}))


class VerdictNormalization(unittest.TestCase):
    def test_mapping_table(self):
        cases = [
            ("sanctions_verdict", "MATCH", "match"), ("sanctions_verdict", "CLEAR", "clear"),
            ("sanctions_verdict", "INVALID", "invalid"), ("sanctions_verdict", "UNKNOWN", "unknown"),
            ("sanctions_verdict", "match", "match"),
            ("sanctions_verdict", "maybe", "unknown"), ("sanctions_verdict", None, "unknown"),
            ("sanctions_verdict", "", "unknown"),
            ("risk_score", "low", "low"), ("risk_score", "HIGH", "high"),
            ("risk_score", "invalid", "invalid"), ("risk_score", None, "unknown"),
            ("risk_score", "critical", "unknown"),
        ]
        for field, raw, expected in cases:
            with self.subTest(field=field, raw=raw):
                self.assertEqual(normalize_verdict(field, raw), expected)

    def test_unknown_field_raises(self):
        with self.assertRaises(ValueError):
            normalize_verdict("decision", "clear")

    def test_idempotent(self):
        for field, values in CANONICAL_VALUES.items():
            for v in values:
                self.assertEqual(normalize_verdict(field, v), v)

    def test_audit_and_eval_values_comparable(self):
        """Raw audit-record verdicts normalize into the eval contract's enums."""
        eval_schema = json.loads((ROOT / "contracts" / "eval-test-case.v1.schema.json").read_text("utf-8"))
        props = eval_schema["properties"]
        for _, _, _, sanctions, enrichment in SCENARIOS.values():
            if sanctions["subagent_result"]:
                v = normalize_verdict("sanctions_verdict", sanctions["subagent_result"]["verdict"])
                self.assertIn(v, props["expected_sanctions_verdict"]["enum"])
            if enrichment["subagent_result"]:
                v = normalize_verdict("risk_score", enrichment["subagent_result"]["risk_score"])
                self.assertIn(v, props["expected_risk_score"]["enum"])

    def test_matches_coordinator_normalizers(self):
        import coordinator
        for raw in ["MATCH", "clear", "Invalid", "UNKNOWN", "maybe", None, ""]:
            self.assertEqual(normalize_verdict("sanctions_verdict", raw),
                             coordinator._normalize_sanctions_verdict(raw))
        for raw in ["low", "MEDIUM", "high", "invalid", "unknown", "critical", None]:
            self.assertEqual(normalize_verdict("risk_score", raw),
                             coordinator._normalize_risk_score(raw))


class NewContractExamples(unittest.TestCase):
    """The agent-identity and policy-rule schemas have no existing producer in
    this repo, so these check the examples used in docs/contracts.md."""

    def test_identity_and_policy_examples(self):
        identity, rule = _validator("agent-identity"), _validator("tool-call-policy-rule")
        identity.validate({
            "agent_id": "sanctions-screening", "name": "Sanctions screening subagent",
            "version": "1.0.0", "owner": "financial-crime-engineering",
            "purpose": "Screen a sender name against the sanctions watchlist.",
            "autonomy": "human_on_the_loop",
        })
        rule.validate({
            "rule_id": "sanctions_may_check_list",
            "description": "Sanctions subagent may call its one tool.",
            "agent_id": "sanctions-screening", "tool": "check_sanctions_list", "effect": "allow",
        })
        rule.validate({
            "rule_id": "large_transfer_needs_approval",
            "description": "Any transfer over 10000 needs a human.",
            "agent_id": "*", "tool": "initiate_transfer", "effect": "require_human_approval",
            "conditions": [{"argument": "amount", "operator": "gt", "value": 10000}],
        })
        self.assertFalse(rule.is_valid({"rule_id": "r", "description": "d", "agent_id": "*",
                                        "tool": "t", "effect": "maybe"}))
        self.assertFalse(identity.is_valid({"agent_id": "Bad_ID"}))


if __name__ == "__main__":
    unittest.main()
