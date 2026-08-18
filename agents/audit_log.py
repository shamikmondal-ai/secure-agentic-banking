"""Structured, append-only, tamper-evident audit logging for transaction
decisions.

Every call to log_transaction() appends one JSON object (one line -- JSON
Lines / JSONL) to audit/transactions.jsonl. Each record stores the SHA-256
hash of the immediately preceding record ("prev_record_hash") and its own
hash ("record_hash"), computed over every other field. This forms a hash
chain: changing or deleting any past record changes what its neighbors'
hashes should be, so tampering is detectable by replaying the chain from
the top -- see verify_log() -- without needing a database, a signing key,
or an external service.

What this does and does not protect against is explained where
coordinator.py calls into this module, and in the surrounding project
documentation. In short: this detects tampering with records already
written to this file. It does not prevent someone with write access from
truncating the file and starting a new, internally-consistent chain, or
from editing the file and this code together. Real tamper-evidence for a
regulated system needs this file (or its hash chain) mirrored to storage
the application itself cannot rewrite -- see the note at the bottom of
this file.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG_PATH = Path(__file__).resolve().parent.parent / "audit" / "transactions.jsonl"
GENESIS_HASH = "0" * 64


def _canonical_json(obj: dict) -> str:
    """Deterministic JSON serialization used for hashing: sorted keys, no
    extraneous whitespace, so the same logical record always hashes the same
    way regardless of how it was constructed."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _hash_record(record_without_hash: dict) -> str:
    return hashlib.sha256(_canonical_json(record_without_hash).encode("utf-8")).hexdigest()


def _read_last_record_hash(path: Path) -> str:
    """Return the record_hash of the last line in the log, or the genesis
    hash if the log doesn't exist yet or is empty."""
    if not path.exists():
        return GENESIS_HASH
    last_line = None
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last_line = line
    if last_line is None:
        return GENESIS_HASH
    return json.loads(last_line)["record_hash"]


def log_transaction(
    *,
    sender_name: str,
    customer_id: str,
    amount: float,
    sanctions_info: dict,
    enrichment_info: dict,
    decision: str,
    confidence: str,
    routing: str,
    path: Path = AUDIT_LOG_PATH,
) -> dict:
    """Append one complete audit record for a processed transaction.

    sanctions_info / enrichment_info are the dicts produced by
    coordinator._screen_sender() / _enrich_customer(): each carries whether
    boundary validation passed, the rejection reason if not, and the
    subagent's full structured result (or None if the subagent was never
    called because boundary validation rejected the input first).

    Returns the record that was written, including its computed hash.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "record_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": {
            "sender_name": sender_name,
            "customer_id": customer_id,
            "amount": amount,
        },
        "sanctions_screening": {
            "boundary_validation_passed": sanctions_info["boundary_valid"],
            "boundary_rejection_reason": sanctions_info["boundary_reason"],
            "subagent_called": sanctions_info["subagent_result"] is not None,
            "subagent_result": sanctions_info["subagent_result"],
        },
        "enrichment": {
            "boundary_validation_passed": enrichment_info["boundary_valid"],
            "boundary_rejection_reason": enrichment_info["boundary_reason"],
            "subagent_called": enrichment_info["subagent_result"] is not None,
            "subagent_result": enrichment_info["subagent_result"],
        },
        "decision": decision,
        "confidence": confidence,
        "routing": routing,
    }

    record["prev_record_hash"] = _read_last_record_hash(path)
    record["record_hash"] = _hash_record(record)

    with path.open("a", encoding="utf-8") as f:
        f.write(_canonical_json(record) + "\n")

    return record


def verify_log(path: Path = AUDIT_LOG_PATH) -> dict:
    """Replay the hash chain from the top and confirm every record's
    record_hash matches its actual content, and that each record's
    prev_record_hash correctly links to the record before it.

    Returns {"valid": bool, "records_checked": int, "problems": [str, ...]}
    rather than raising, so a caller (a CLI, a CI check, a human) can decide
    how to react to a broken chain.
    """
    if not path.exists():
        return {"valid": True, "records_checked": 0, "problems": []}

    problems = []
    expected_prev = GENESIS_HASH
    count = 0
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            count += 1
            record = json.loads(line)
            stored_hash = record.get("record_hash")
            stored_prev = record.get("prev_record_hash")

            recomputed_input = dict(record)
            recomputed_input.pop("record_hash", None)
            actual_hash = _hash_record(recomputed_input)

            if stored_prev != expected_prev:
                problems.append(
                    f"line {line_number} ({record.get('record_id')}): prev_record_hash "
                    f"{stored_prev!r} does not match the hash of the preceding record "
                    f"{expected_prev!r} -- a record may have been inserted, removed, or reordered"
                )
            if actual_hash != stored_hash:
                problems.append(
                    f"line {line_number} ({record.get('record_id')}): record_hash {stored_hash!r} "
                    f"does not match the recomputed hash {actual_hash!r} -- this record's content "
                    f"has been altered since it was written"
                )
            expected_prev = stored_hash

    return {"valid": len(problems) == 0, "records_checked": count, "problems": problems}


if __name__ == "__main__":
    report = verify_log()
    if report["valid"]:
        print(f"OK -- {report['records_checked']} audit record(s), hash chain intact.")
    else:
        print(f"TAMPERING DETECTED across {report['records_checked']} record(s):")
        for problem in report["problems"]:
            print(f"  - {problem}")
        raise SystemExit(1)
