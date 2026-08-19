"""Coordinator agent: orchestrates the sanctions-screening and enrichment
subagents for a transaction, combines their structured verdicts into a
single decision, decides whether a human needs to see it, and writes a
complete audit record of the whole thing.

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

Whether a transaction needs a human is decided by an explicit, named,
independently-configurable set of rules (see routing_rules.py), not by a
single derived score -- see that module and process_transaction() below
for what each rule catches and why routing by signal beats a uniform
policy (auto-decide everything, or review a random sample of everything).

Every processed transaction is logged via audit_log.log_transaction(); any
transaction routed to review is also written to the human review queue via
review_queue.enqueue_for_review() -- see review_queue.py.
"""

import logging
import uuid

import logging_config  # noqa: F401  (side effect: configures logging)
from audit_log import log_transaction
from enrichment import enrich_customer
from review_queue import enqueue_for_review
from routing_rules import evaluate_review_rules
from sanctions_screening import screen_name
from validation import is_valid_customer_id, is_valid_name

logger = logging.getLogger(__name__)

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
    subagent was never called), and the normalized verdict used downstream.
    """
    if not is_valid_name(sender_name):
        logger.warning(
            "sender_name failed input validation; sanctions subagent not called",
            extra={"context": {"sender_name": sender_name}},
        )
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
    subagent was never called), and the normalized risk score used
    downstream.
    """
    if not is_valid_customer_id(customer_id):
        logger.warning(
            "customer_id failed input validation; enrichment subagent not called",
            extra={"context": {"customer_id": customer_id}},
        )
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


def _decide(sanctions_verdict: str, review_reasons: list) -> str:
    """The final decision.

    A confirmed sanctions match always blocks -- it's the one outcome
    conservative enough to auto-decide without waiting for a human, because
    it's the safe direction to auto-decide in (see
    docs/governance-mapping.md's Control 6 discussion of auto-block as a
    "human-on-the-loop," not "human-in-the-loop," pattern -- a blocked party
    still needs a path to human review after the fact, just not before the
    block takes effect).

    Otherwise, any fired review rule routes to human review; a transaction
    is only auto-cleared if zero rules fired. This is what makes "clear"
    the outcome that has to earn its way there, rather than the default.
    """
    if sanctions_verdict == "match":
        return "block"
    return "review" if review_reasons else "clear"


def _combine_confidence(sanctions_info: dict, enrichment_info: dict) -> str:
    """Derive a descriptive overall confidence from the two subagents'
    self-reported confidence fields. This is informational -- logged
    alongside the decision for a human's benefit -- and does not itself
    drive routing; routing_rules.py's low_confidence rule inspects each
    subagent's raw self-reported confidence directly, which is a more
    precise signal than this summary.

    A rejected or indeterminate field can never yield "high" or "medium"
    here, regardless of what the other subagent reported -- an unresolved
    half of the picture caps how confident the whole summary can claim to
    be.
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
    """Orchestrate a transaction through both subagents, decide, route,
    log a complete audit record, enqueue for human review if needed, and
    return the decision.

    Each raw identifier is validated before it is ever handed to a
    subagent; only a conforming identifier is delegated. The decision is
    based solely on each subagent's structured verdict field (or on the
    boundary rejection) -- never on prose. Whether the transaction needs a
    human is decided by routing_rules.evaluate_review_rules(), an explicit,
    named, independently-configurable rule set -- not a single score.
    """
    record_id = str(uuid.uuid4())
    logger.info(
        "Processing transaction",
        extra={
            "context": {
                "record_id": record_id,
                "sender_name": sender_name,
                "customer_id": customer_id,
                "amount": amount,
            }
        },
    )

    sanctions_info = _screen_sender(sender_name)
    enrichment_info = _enrich_customer(customer_id)

    sanctions_verdict = sanctions_info["verdict"]
    risk_score = enrichment_info["verdict"]

    review_reasons = evaluate_review_rules(
        sanctions_verdict, risk_score, sanctions_info, enrichment_info, amount
    )
    decision = _decide(sanctions_verdict, review_reasons)
    confidence = _combine_confidence(sanctions_info, enrichment_info)
    routing = "human_review_required" if decision == "review" else "auto_decided"

    logger.info(
        "Transaction decided",
        extra={
            "context": {
                "record_id": record_id,
                "decision": decision,
                "confidence": confidence,
                "routing": routing,
                "review_reasons": [r["rule"] for r in review_reasons],
            }
        },
    )

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
        record_id=record_id,
        sender_name=sender_name,
        customer_id=customer_id,
        amount=amount,
        sanctions_info=sanctions_info,
        enrichment_info=enrichment_info,
        decision=decision,
        confidence=confidence,
        routing=routing,
        review_reasons=review_reasons,
    )

    if decision == "review":
        enqueue_for_review(
            record_id,
            {
                "input": {
                    "sender_name": sender_name,
                    "customer_id": customer_id,
                    "amount": amount,
                },
                "decision": decision,
                "confidence": confidence,
                "sanctions_verdict": sanctions_verdict,
                "risk_score": risk_score,
                "review_reasons": review_reasons,
                "sanctions_report": sanctions_report,
                "enrichment_report": enrichment_report,
            },
        )

    return {
        "sender_name": sender_name,
        "customer_id": customer_id,
        "amount": amount,
        "sanctions_verdict": sanctions_verdict,
        "risk_score": risk_score,
        "decision": decision,
        "confidence": confidence,
        "routing": routing,
        "review_reasons": review_reasons,
        "sanctions_report": sanctions_report,
        "enrichment_report": enrichment_report,
    }
