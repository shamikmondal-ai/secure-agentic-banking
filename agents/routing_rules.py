"""Explicit, configurable rules deciding whether a transaction needs human
review.

Each rule below is a small, independently named function: it inspects the
already-computed sanctions/enrichment results and returns a reason string
if it fires, or None if it doesn't. Rules are not mutually exclusive -- a
transaction can (and should) show every reason that genuinely applies, not
just the first one found, because a human reading the review queue needs
to know all of why something was flagged, not just enough to flag it once.

Configuration is two settings from config/settings.py, re-exported here
under the same names so existing references to
routing_rules.AMOUNT_REVIEW_THRESHOLD / routing_rules.ENABLED_REVIEW_RULES
keep working:

- AMOUNT_REVIEW_THRESHOLD (env: AMOUNT_REVIEW_THRESHOLD) -- the dollar
  amount above which size alone routes to review.
- ENABLED_REVIEW_RULES (env: ENABLED_REVIEW_RULES, comma-separated) --
  which named rules are actually active. Removing a name here disables
  that rule with no code change elsewhere.
"""

import logging_config  # noqa: F401  (side effect: configures logging + fixes sys.path)
from config.settings import AMOUNT_REVIEW_THRESHOLD, ENABLED_REVIEW_RULES  # noqa: F401


def _rule_low_confidence(ctx: dict) -> str | None:
    """Fires when either subagent explicitly self-reported low confidence
    in its own verdict -- the model itself flagging uncertainty, not a
    derived or inferred signal."""
    for label, info in (
        ("sanctions_screening", ctx["sanctions_info"]),
        ("enrichment", ctx["enrichment_info"]),
    ):
        result = info["subagent_result"]
        if result is not None and result.get("confidence") == "low":
            return f"{label} subagent self-reported low confidence in its verdict"
    return None


def _rule_conflicting_signals(ctx: dict) -> str | None:
    """Fires when the two subagents' verdicts point in different
    directions: a clean sanctions check on a high-risk customer, or a
    sanctions match on an otherwise low-risk customer. Neither field is
    wrong on its own here -- the disagreement between them is the signal."""
    sanctions_verdict, risk_score = ctx["sanctions_verdict"], ctx["risk_score"]
    if sanctions_verdict == "clear" and risk_score == "high":
        return "sanctions check is clear but customer risk score is high"
    if sanctions_verdict == "match" and risk_score == "low":
        return "sanctions check matched but customer risk score is low"
    return None


def _rule_validation_flagged(ctx: dict) -> str | None:
    """Fires when either identifier was rejected by input validation (see
    validation.py) before or during subagent screening -- the input itself
    is the problem, not the model's judgment about it."""
    flags = []
    if ctx["sanctions_verdict"] == "invalid":
        flags.append("sender_name failed input validation")
    if ctx["risk_score"] == "invalid":
        flags.append("customer_id failed input validation")
    return "; ".join(flags) if flags else None


def _rule_unresolved_lookup(ctx: dict) -> str | None:
    """Fires when a lookup ran on well-formed input but produced no usable
    answer -- a customer ID not on file, or a subagent call that never
    returned a valid structured verdict (a refusal, a parse failure).
    Distinct from validation_flagged: the input was fine, the answer just
    isn't there."""
    flags = []
    if ctx["sanctions_verdict"] == "unknown":
        flags.append("sanctions screening returned no usable verdict")
    if ctx["risk_score"] == "unknown":
        flags.append("enrichment returned no usable risk score")
    return "; ".join(flags) if flags else None


def _rule_amount_exceeds_threshold(ctx: dict) -> str | None:
    """Fires when the transaction amount exceeds AMOUNT_REVIEW_THRESHOLD,
    independent of how clean the screening otherwise looks -- size alone is
    a reason for a second set of eyes."""
    if ctx["amount"] > AMOUNT_REVIEW_THRESHOLD:
        return (
            f"amount {ctx['amount']:,.2f} exceeds review threshold "
            f"{AMOUNT_REVIEW_THRESHOLD:,.2f}"
        )
    return None


def _rule_elevated_risk_tier(ctx: dict) -> str | None:
    """Fires whenever risk_score is medium or high, regardless of the
    sanctions outcome. Kept distinct from conflicting_signals (which only
    fires on a high/clear mismatch) so a medium-risk customer is never
    auto-cleared just because nothing else about the transaction looks
    unusual."""
    if ctx["risk_score"] in ("medium", "high"):
        return f"risk_score is {ctx['risk_score']}"
    return None


REVIEW_RULES = [
    ("low_confidence", _rule_low_confidence),
    ("conflicting_signals", _rule_conflicting_signals),
    ("validation_flagged", _rule_validation_flagged),
    ("unresolved_lookup", _rule_unresolved_lookup),
    ("amount_exceeds_threshold", _rule_amount_exceeds_threshold),
    ("elevated_risk_tier", _rule_elevated_risk_tier),
]


def evaluate_review_rules(
    sanctions_verdict: str,
    risk_score: str,
    sanctions_info: dict,
    enrichment_info: dict,
    amount: float,
) -> list:
    """Run every enabled rule and return the ones that fired, each as
    {"rule": name, "detail": human-readable reason}."""
    ctx = {
        "sanctions_verdict": sanctions_verdict,
        "risk_score": risk_score,
        "sanctions_info": sanctions_info,
        "enrichment_info": enrichment_info,
        "amount": amount,
    }
    reasons = []
    for name, rule_fn in REVIEW_RULES:
        if name not in ENABLED_REVIEW_RULES:
            continue
        detail = rule_fn(ctx)
        if detail:
            reasons.append({"rule": name, "detail": detail})
    return reasons
