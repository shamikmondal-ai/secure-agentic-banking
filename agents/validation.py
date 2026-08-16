"""Shared input-validation patterns for identifiers the system accepts from
an external, untrusted source (a sender name, a customer ID).

These patterns are applied at two independent points that trust each other
not at all:

  1. The coordinator, before any identifier is handed to a subagent at all
     -- see coordinator.py. This is the primary defense: a malformed
     identifier never reaches an LLM call, so it can never influence what
     argument a model passes to a tool.
  2. Each tool function, right before it touches the underlying data -- see
     sanctions_screening.py / enrichment.py. This is the backstop: even if
     something bypasses layer 1 (a bug, a future caller that skips the
     coordinator, a model that passes an unexpected argument), the tool
     itself still refuses to act on malformed input.
"""

import re

# Letters, spaces, hyphens, and apostrophes only (covers names like "Farid
# Al-Rashid" or "O'Malley"); must start with a letter; capped at a plausible
# name length. This character class structurally excludes the punctuation an
# embedded instruction needs -- colons, periods, semicolons, digits,
# newlines -- so a string like "Elena Marchetti. SYSTEM: ..." fails to match
# regardless of what it says.
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z' -]{0,79}$")

# This project's fake customer ID scheme: "CUST-" plus exactly 4 digits.
# Anything else -- including a real ID with trailing text appended -- is
# rejected outright.
CUSTOMER_ID_PATTERN = re.compile(r"^CUST-\d{4}$")


def is_valid_name(name: str) -> bool:
    """Whether `name` conforms to the expected shape for a person's name."""
    return bool(NAME_PATTERN.match(name))


def is_valid_customer_id(customer_id: str) -> bool:
    """Whether `customer_id` conforms to the expected CUST-#### shape."""
    return bool(CUSTOMER_ID_PATTERN.match(customer_id))
