"""Coordinator agent: orchestrates the sanctions-screening and enrichment
subagents for a transaction, then combines their structured verdicts into a
single decision.

The coordinator never screens or enriches anything itself -- it holds no
sanctions list, no risk table, and no lookup tool of its own. It also never
makes a decision by reading free text: each subagent returns a structured
dict (see sanctions_screening.py / enrichment.py) whose "verdict" /
"risk_score" field is schema-constrained by the Anthropic API's structured
outputs feature, separate from a free-text "explanation" field. The
coordinator reads only the constrained field.

Before either subagent is even called, the coordinator validates the raw
sender_name and customer_id against the expected identifier shape (see
validation.py). A non-conforming identifier is rejected here -- it never
becomes part of a message sent to a subagent, and so never has a chance to
be echoed into a tool argument. This is the first of two independent
validation layers; the second lives inside each tool function itself (see
check_sanctions_list / get_customer_risk_profile), as a backstop in case
something ever bypasses this one.
"""

from enrichment import enrich_customer
from sanctions_screening import screen_name
from validation import is_valid_customer_id, is_valid_name

VALID_SANCTIONS_VERDICTS = {"match", "clear", "invalid"}
VALID_RISK_SCORES = {"low", "medium", "high", "invalid"}


def _normalize_sanctions_verdict(raw: str) -> str:
    verdict = str(raw).lower()
    return verdict if verdict in VALID_SANCTIONS_VERDICTS else "unknown"


def _normalize_risk_score(raw: str) -> str:
    score = str(raw).lower()
    return score if score in VALID_RISK_SCORES else "unknown"


def _screen_sender(sender_name: str) -> tuple[str, str]:
    """Validate then delegate the sender name to the sanctions subagent.

    Returns (verdict, report). A malformed name is rejected here, before the
    subagent is ever called.
    """
    if not is_valid_name(sender_name):
        return "invalid", (
            "Rejected before reaching the sanctions-screening subagent: sender_name does "
            "not conform to the expected name format (letters, spaces, hyphens, and "
            "apostrophes only)."
        )
    result = screen_name(sender_name)
    return _normalize_sanctions_verdict(result.get("verdict", "")), result.get("explanation", "")


def _enrich_customer(customer_id: str) -> tuple[str, str]:
    """Validate then delegate the customer ID to the enrichment subagent.

    Returns (risk_score, report). A malformed customer ID is rejected here,
    before the subagent is ever called.
    """
    if not is_valid_customer_id(customer_id):
        return "invalid", (
            "Rejected before reaching the enrichment subagent: customer_id does not "
            "conform to the expected CUST-#### format."
        )
    result = enrich_customer(customer_id)
    return _normalize_risk_score(result.get("risk_score", "")), result.get("explanation", "")


def _decide(sanctions_verdict: str, risk_score: str) -> str:
    """Combine the two subagents' structured verdicts into a single decision.

    This is plain synthesis over two already-computed, schema-validated (or
    boundary-rejected) fields -- no screening or enrichment logic, and no
    text scanning, lives here. A rejected ("invalid") or indeterminate
    ("unknown") field is never treated as safe -- it always forces review.
    """
    if sanctions_verdict == "match":
        return "block"
    if sanctions_verdict in ("unknown", "invalid") or risk_score in ("unknown", "invalid"):
        return "review"
    if risk_score in ("high", "medium"):
        return "review"
    return "clear"


def process_transaction(sender_name: str, customer_id: str, amount: float) -> dict:
    """Orchestrate a transaction through both subagents and return a
    structured decision.

    Each raw identifier is validated before it is ever handed to a subagent;
    only a conforming identifier is delegated. The coordinator's decision is
    based solely on each subagent's structured verdict field (or on the
    boundary rejection) -- never on prose.
    """
    sanctions_verdict, sanctions_report = _screen_sender(sender_name)
    risk_score, enrichment_report = _enrich_customer(customer_id)

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
