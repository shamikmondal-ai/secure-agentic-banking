"""A simple, file-based human review queue.

Every transaction the coordinator routes to review is written here as one
JSON file per pending item, under audit/review_queue/pending/. Resolving an
item moves it to audit/review_queue/resolved/, with the reviewer's decision,
identity, and any notes appended -- so a resolved item still carries a full
record of who decided what, alongside the machine's own reasoning.

This is deliberately a filesystem queue, not a database or a ticketing
system -- it's the simplest thing that gives a human something concrete to
look at (`python agents/review_queue.py` lists what's pending) and gives
"was this actually reviewed" an auditable answer, which is the specific gap
named in docs/governance-mapping.md's Control 6 write-up: routing to
review was previously a label with no enforced workflow behind it.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import logging_config  # noqa: F401  (side effect: configures logging)

logger = logging.getLogger(__name__)

QUEUE_ROOT = Path(__file__).resolve().parent.parent / "audit" / "review_queue"
PENDING_DIR = QUEUE_ROOT / "pending"
RESOLVED_DIR = QUEUE_ROOT / "resolved"


def enqueue_for_review(record_id: str, item: dict) -> Path:
    """Write one pending review item. `item` should carry everything a
    human needs to decide without re-running anything: the transaction
    input, the machine's decision and confidence, and which review rules
    fired and why."""
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    path = PENDING_DIR / f"{record_id}.json"
    payload = {
        "record_id": record_id,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        **item,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(
        "Transaction enqueued for human review",
        extra={"context": {"record_id": record_id, "decision": item.get("decision")}},
    )
    return path


def list_pending_reviews() -> list:
    """Return every pending item, oldest first."""
    if not PENDING_DIR.exists():
        return []
    items = [json.loads(path.read_text(encoding="utf-8")) for path in PENDING_DIR.glob("*.json")]
    return sorted(items, key=lambda item: item["queued_at"])


def resolve_review(record_id: str, human_decision: str, reviewer: str, notes: str = "") -> Path:
    """Move a pending item to resolved/, recording who decided what and
    when. Raises FileNotFoundError if the item isn't (or is no longer)
    pending."""
    pending_path = PENDING_DIR / f"{record_id}.json"
    item = json.loads(pending_path.read_text(encoding="utf-8"))
    item["resolution"] = {
        "human_decision": human_decision,
        "reviewer": reviewer,
        "notes": notes,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    RESOLVED_DIR.mkdir(parents=True, exist_ok=True)
    resolved_path = RESOLVED_DIR / f"{record_id}.json"
    resolved_path.write_text(json.dumps(item, indent=2), encoding="utf-8")
    pending_path.unlink()
    logger.info(
        "Review resolved",
        extra={
            "context": {
                "record_id": record_id,
                "human_decision": human_decision,
                "reviewer": reviewer,
            }
        },
    )
    return resolved_path


if __name__ == "__main__":
    pending = list_pending_reviews()
    if not pending:
        print("No items pending review.")
    else:
        print(f"{len(pending)} item(s) pending review:\n")
        for item in pending:
            print(f"--- {item['record_id']} (queued {item['queued_at']}) ---")
            print(f"  sender_name:  {item['input']['sender_name']!r}")
            print(f"  customer_id:  {item['input']['customer_id']!r}")
            print(f"  amount:       {item['input']['amount']}")
            print(f"  decision:     {item['decision']}  (confidence: {item['confidence']})")
            print("  reasons:")
            for reason in item["review_reasons"]:
                print(f"    - [{reason['rule']}] {reason['detail']}")
            print()
