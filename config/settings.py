"""Central configuration for the transaction-triage pipeline.

Every setting here is a plain constant with a sensible default, overridable
via environment variable -- the same .env-based mechanism the rest of this
project already uses (see check_setup.py). No new config file format or
parser: one obvious place to find and change a threshold or a model name,
instead of hunting through agents/*.py for a hardcoded value.

AMOUNT_REVIEW_THRESHOLD and ENABLED_REVIEW_RULES keep the env var names
they already had before this file existed (routing_rules.py used to define
them locally); everything else introduced by this change uses a TRIAGE_
prefix, since "MODEL" or "MAX_TOKENS" alone are too generic to safely own
as bare environment variable names.
"""

import os


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_set(name: str, default: set) -> set:
    raw = os.environ.get(name)
    if raw is None:
        return set(default)
    return {item.strip() for item in raw.split(",") if item.strip()}


# -- Model --------------------------------------------------------------------
MODEL = _env_str("TRIAGE_MODEL", "claude-opus-5")
MAX_TOKENS = _env_int("TRIAGE_MAX_TOKENS", 1024)

# -- Retries around subagent API calls -----------------------------------------
# The Anthropic SDK already retries connection errors, rate limits, and
# 5xx/overload errors internally (max_retries, default 2). These settings
# add one more layer above that -- see agents/api_utils.py -- for the case
# observed in practice: a sustained overload that outlasts the SDK's own
# retry budget.
API_MAX_RETRIES = _env_int("TRIAGE_API_MAX_RETRIES", 3)
API_RETRY_BASE_DELAY_SECONDS = _env_float("TRIAGE_API_RETRY_BASE_DELAY_SECONDS", 1.0)

# -- Routing thresholds ---------------------------------------------------------
AMOUNT_REVIEW_THRESHOLD = _env_float("AMOUNT_REVIEW_THRESHOLD", 10_000.0)
ENABLED_REVIEW_RULES = _env_set(
    "ENABLED_REVIEW_RULES",
    {
        "low_confidence",
        "conflicting_signals",
        "validation_flagged",
        "unresolved_lookup",
        "amount_exceeds_threshold",
        "elevated_risk_tier",
    },
)

# -- Logging --------------------------------------------------------------------
LOG_LEVEL = _env_str("LOG_LEVEL", "INFO")
