"""Canonical verdict vocabulary shared across the audit-event and
eval-test-case contracts.

Audit records keep the subagent's raw output ("MATCH"); eval cases use the
coordinator's normalized lowercase form ("match"). normalize_verdict() maps
either to the canonical lowercase value so values from the two can be
compared. See the verdict mapping table in docs/contracts.md.

Behavior matches coordinator._normalize_sanctions_verdict() and
_normalize_risk_score(): lowercase, and anything outside the known set
becomes "unknown" rather than raising.
"""

CANONICAL_VALUES = {
    "sanctions_verdict": ("match", "clear", "invalid", "unknown"),
    "risk_score": ("low", "medium", "high", "invalid", "unknown"),
}


def normalize_verdict(field: str, raw) -> str:
    """Return the canonical lowercase value of `raw` for `field`
    ("sanctions_verdict" or "risk_score"); "unknown" if it isn't a known
    value. Raises ValueError for an unrecognized `field`."""
    try:
        allowed = CANONICAL_VALUES[field]
    except KeyError:
        raise ValueError(f"unknown verdict field {field!r}; expected one of {sorted(CANONICAL_VALUES)}")
    value = str(raw).lower()
    return value if value in allowed else "unknown"
