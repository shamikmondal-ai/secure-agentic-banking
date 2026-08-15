"""Coordinator agent: orchestrates the sanctions-screening and enrichment
subagents for a transaction, then combines their verdicts into a single
structured decision.

The coordinator never screens or enriches anything itself -- it holds no
sanctions list, no risk table, and no lookup tool of its own. It only knows
how to ask each subagent for an outcome (screen this name, look up this
customer) and how to combine the two answers into a decision. All of the
domain work happens inside screen_name() and enrich_customer(), each of
which is itself a full single-tool Claude agent (see sanctions_screening.py
and enrichment.py).
"""

import re

from enrichment import enrich_customer
from sanctions_screening import screen_name

SANCTIONS_MATCH_RE = re.compile(r"\bmatch\b", re.IGNORECASE)
SANCTIONS_CLEAR_RE = re.compile(r"\bclear\b", re.IGNORECASE)


def _parse_sanctions_verdict(report: str) -> str:
    """Interpret the sanctions-screening subagent's free-text verdict.

    Check CLEAR before MATCH: a clean report reads like "no match found",
    which contains the substring "match" -- checking MATCH first would
    misread a clear result as a hit.
    """
    if SANCTIONS_CLEAR_RE.search(report):
        return "clear"
    if SANCTIONS_MATCH_RE.search(report):
        return "match"
    return "unknown"


def _parse_risk_score(report: str) -> str:
    """Interpret the enrichment subagent's free-text risk score."""
    lowered = report.lower()
    if "high" in lowered:
        return "high"
    if "medium" in lowered:
        return "medium"
    if "low" in lowered:
        return "low"
    return "unknown"


def _decide(sanctions_verdict: str, risk_score: str) -> str:
    """Combine the two subagents' verdicts into a single decision.

    This is plain synthesis over two already-computed verdicts -- no
    screening or enrichment logic lives here.
    """
    if sanctions_verdict == "match":
        return "block"
    if sanctions_verdict == "unknown" or risk_score == "unknown":
        return "review"
    if risk_score in ("high", "medium"):
        return "review"
    return "clear"


def process_transaction(sender_name: str, customer_id: str, amount: float) -> dict:
    """Orchestrate a transaction through both subagents and return a
    structured decision.

    The coordinator delegates the entire screening task to screen_name() and
    the entire enrichment task to enrich_customer() -- it tells each subagent
    what outcome it needs, not how to produce it, and then combines their
    two independent answers.
    """
    sanctions_report = screen_name(sender_name)
    enrichment_report = enrich_customer(customer_id)

    sanctions_verdict = _parse_sanctions_verdict(sanctions_report)
    risk_score = _parse_risk_score(enrichment_report)
    decision = _decide(sanctions_verdict, risk_score)

    return {
        "sender_name": sender_name,
        "customer_id": customer_id,
        "amount": amount,
        "sanctions_verdict": sanctions_verdict,
        "risk_score": risk_score,
        "decision": decision,
        "sanctions_report": sanctions_report,
        "enrichment_report": enrichment_report,
    }
