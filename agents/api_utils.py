"""Retry helper for transient Anthropic API failures.

Wraps a single API call with a bounded number of retries and exponential
backoff, for the error classes the Anthropic SDK itself documents as
retryable: connection errors, rate limits, and server-side 5xx/overload
errors (see shared/error-codes.md). The SDK already retries these
internally (max_retries, default 2); this adds one more layer above it for
what we've actually hit in practice while building this project -- a
sustained overload (HTTP 529) that outlasted the SDK's own retry budget and
crashed the calling script outright.

The point of this module isn't just "retry more" -- it's "fail safely
when retries are exhausted." call_with_retries raises
SubagentUnavailableError on final failure; sanctions_screening.py and
enrichment.py catch that one error and degrade to the same UNKNOWN /
low-confidence structured result they already use for a refusal or a
parse failure, which routing_rules.py's unresolved_lookup rule then routes
to human review. A transient API outage should mean "a human looks at this
transaction," never "the process crashes" or "the transaction silently
clears."
"""

import logging
import random
import time

import anthropic

import logging_config  # noqa: F401  (side effect: configures logging)
from config.settings import API_MAX_RETRIES, API_RETRY_BASE_DELAY_SECONDS

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
)


class SubagentUnavailableError(RuntimeError):
    """Raised when an Anthropic API call fails after every retry is
    exhausted. Callers must catch this and degrade to a safe result --
    never let it propagate and crash the caller outright."""


def call_with_retries(fn, *, max_retries: int = None, base_delay: float = None, description: str = "API call"):
    """Call fn() and retry on a transient failure, with exponential backoff
    plus jitter. Raises SubagentUnavailableError if every attempt fails."""
    max_retries = API_MAX_RETRIES if max_retries is None else max_retries
    base_delay = API_RETRY_BASE_DELAY_SECONDS if base_delay is None else base_delay

    last_error = None
    for attempt in range(1, max_retries + 2):  # +1 for the initial attempt
        try:
            return fn()
        except RETRYABLE_EXCEPTIONS as exc:
            last_error = exc
            if attempt > max_retries:
                break
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, base_delay)
            logger.warning(
                f"{description} failed (attempt {attempt}/{max_retries + 1}), retrying in "
                f"{delay:.1f}s",
                extra={
                    "context": {
                        "attempt": attempt,
                        "max_attempts": max_retries + 1,
                        "error_type": exc.__class__.__name__,
                        "delay_seconds": round(delay, 2),
                    }
                },
            )
            time.sleep(delay)

    logger.error(
        f"{description} failed after {max_retries + 1} attempt(s), giving up",
        extra={
            "context": {
                "max_attempts": max_retries + 1,
                "error_type": last_error.__class__.__name__ if last_error else None,
            }
        },
    )
    raise SubagentUnavailableError(
        f"{description} failed after {max_retries + 1} attempts: {last_error}"
    ) from last_error
