"""Coordinator agent: orchestrates the sanctions-screening and enrichment
subagents for a transaction, combines their structured verdicts into a
single decision, and writes a complete audit record of the whole thing.

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

Every processed transaction is logged via audit_log.log_transaction() --
see that module for the record format and its tamper-evidence properties.
"""

from audit_log import log_transaction
from enrichment import enrich_customer
from sanctions_screening import screen_name
from validation import is_valid_customer_id, is_valid_name

VALID_SANCTIONS_VERDICTS = {"match", "clear", "invalid"}
VALID_RISK_SCORES = {"low", "medium", "high", "invalid"}
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _normalize_sanctions_verdict(raw: str) -> str:
    verdict = str(raw).lower()
    return verdict if verdict in VALID_SANCTIONS_VERDICTS else "unknown"


def _normalize_risk_score(raw: str) -> str:
    score = str(raw).lower()
    return score if score in VALID_RISK_SCORES else "unknown"


def _screen_sender(sender_name: str) -> dict:
    """Validate then delegate the sender name to the sanctions subagent.

    Returns a dict carrying everything needed for both decision-making and
    the audit record: whether boundary validation passed, the rejection
    reason if not, the subagent's full structured result (or None if the
    subagent was never called), and the normalized verdict used by
    _decide().
    """
    if not is_valid_name(sender_name):
        return {
            "boundary_valid": False,
            "boundary_reason": (
                "Rejected before reaching the sanctions-screening subagent: sender_name does "
                "not conform to the expected name format (letters, spaces, hyphens, and "
                "apostrophes only)."
            ),
            "subagent_result": None,
            "verdict": "invalid",
        }
    result = screen_name(sender_name)
    return {
        "boundary_valid": True,
        "boundary_reason": None,
        "subagent_result": result,
        "verdict": _normalize_sanctions_verdict(result.get("verdict", "")),
    }


def _enrich_customer(customer_id: str) -> dict:
    """Validate then delegate the customer ID to the enrichment subagent.

    Returns a dict carrying everything needed for both decision-making and
    the audit record: whether boundary validation passed, the rejection
    reason if not, the subagent's full structured result (or None if the
    subagent was never called), and the normalized risk score used by
    _decide().
    """
    if not is_valid_customer_id(customer_id):
        return {
            "boundary_valid": False,
            "boundary_reason": (
                "Rejected before reaching the enrichment subagent: customer_id does not "
                "conform to the expected CUST-#### format."
            ),
            "subagent_result": None,
            "verdict": "invalid",
        }
    result = enrich_customer(customer_id)
    return {
        "boundary_valid": True,
        "boundary_reason": None,
        "subagent_result": result,
        "verdict": _normalize_risk_score(result.get("risk_score", "")),
    }


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


def _combine_confidence(sanctions_info: dict, enrichment_info: dict) -> str:
    """Derive an overall confidence from the two subagents' self-reported
    confidence fields.

    A rejected or indeterminate field can never yield "high" or "medium"
    overall confidence, regardless of what the other subagent reported --
    an unresolved half of the picture caps how confident the whole decision
    can be. Otherwise, confidence is the weaker of the two subagents' own
    self-reported confidence, the same "weakest link" pattern _decide()
    uses for the decision itself.
    """
    if sanctions_info["verdict"] in ("unknown", "invalid"):
        return "low"
    if enrichment_info["verdict"] in ("unknown", "invalid"):
        return "low"

    sanctions_confidence = (sanctions_info["subagent_result"] or {}).get("confidence", "low")
    enrichment_confidence = (enrichment_info["subagent_result"] or {}).get("confidence", "low")

    weaker_rank = min(
        CONFIDENCE_RANK.get(sanctions_confidence, 0),
        CONFIDENCE_RANK.get(enrichment_confidence, 0),
    )
    return {0: "low", 1: "medium", 2: "high"}[weaker_rank]


def process_transaction(sender_name: str, customer_id: str, amount: float) -> dict:
    """Orchestrate a transaction through both subagents, decide, log a
    complete audit record of the whole thing, and return the decision.

    Each raw identifier is validated before it is ever handed to a
    subagent; only a conforming identifier is delegated. The decision is
    based solely on each subagent's structured verdict field (or on the
    boundary rejection) -- never on prose.
    """
    sanctions_info = _screen_sender(sender_name)
    enrichment_info = _enrich_customer(customer_id)

    sanctions_verdict = sanctions_info["verdict"]
    risk_score = enrichment_info["verdict"]
    decision = _decide(sanctions_verdict, risk_score)
    confidence = _combine_confidence(sanctions_info, enrichment_info)
    routing = "human_review_required" if decision == "review" else "auto_decided"

    sanctions_report = (
        sanctions_info["subagent_result"]["explanation"]
        if sanctions_info["subagent_result"] is not None
        else sanctions_info["boundary_reason"]
    )
    enrichment_report = (
        enrichment_info["subagent_result"]["explanation"]
        if enrichment_info["subagent_result"] is not None
        else enrichment_info["boundary_reason"]
    )

    log_transaction(
        sender_name=sender_name,
        customer_id=customer_id,
        amount=amount,
        sanctions_info=sanctions_info,
        enrichment_info=enrichment_info,
        decision=decision,
        confidence=confidence,
        routing=routing,
    )

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
