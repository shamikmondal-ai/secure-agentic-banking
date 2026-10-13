# Shared contracts

Versioned JSON Schemas that sibling repos share. They live in `contracts/`:

| Contract | File | Status | Source |
|---|---|---|---|
| Audit event | `contracts/audit-event.v1.schema.json` | stable | Derived from `agents/audit_log.py` `log_transaction()` |
| Eval test case | `contracts/eval-test-case.v1.schema.json` | stable | Derived from `evals/test_cases.py` |
| Agent identity | `contracts/agent-identity.v1.schema.json` | **status: draft** | New design (no existing producer) |
| Tool-call policy rule | `contracts/tool-call-policy-rule.v1.schema.json` | **status: draft** | New design (no existing producer) |

The two draft contracts may change without a version bump until the gateway implements them. After that they follow the normal versioning rules below.

`tests/test_contracts.py` proves this repo conforms. It writes audit records through the real `log_transaction()` into a temp file, covering the clear, block, review and boundary-rejected paths. It validates every entry in `TEST_CASES`. It also checks that malformed data is rejected, so the schemas can't pass by being too loose. Run it from the repo root with `python -m unittest tests.test_contracts`. It needs `jsonschema` and `rfc3339-validator` (the latter makes `date-time` format checking actually run), both in `requirements.txt`.

## Cross-cutting design choices

- **JSON Schema draft 2020-12.** It's a standard with validators in every common language, so sibling repos in other languages can use the same files.
- **Version in the filename and `$id`, not in the data.** The path (`audit-event.v1.schema.json`, `.../audit-event/v1`) carries the version. The audit event can't carry a `schema_version` field. Records are hash-chained over every field, so adding one would change what new records hash to. Records already written would also fail a `required` field. Putting the version in the schema identity means this repo conforms with no change to the log format.
- **A breaking change means a new file, `v2`.** Both versions can coexist while consumers migrate. Adding a field to a closed object (see below) is also breaking, so `v1` stays frozen.
- **`additionalProperties: false` on contract objects.** Unknown fields fail validation, so drift between producers and consumers is caught at the contract. The cost is that every new field needs a new version. For an audit trail I judge that the right trade.
- **Closed enums.** `decision`, `routing`, `confidence`, `category` and the rest are fixed sets. A consumer can then branch on them exhaustively.
- **Schemas describe what the code does today, not what it should do.** Known gaps are left visible rather than hidden (see the negative `amount` below).

## Audit event

- **Field layout is copied verbatim from `log_transaction()`.** `input`, `sanctions_screening`, `enrichment`, `decision`, `confidence`, `routing`, `review_reasons` and the two hashes are all required. The log is append-only, so every record must be complete.
- **`review_reasons` is an array of `{rule, detail}` objects, not strings.** This is what the coordinator actually passes in, and the schema follows the code. The review queue uses plain rule names, but the audit log keeps the human-readable `detail` for the reviewer.
- **Hashes are `^[0-9a-f]{64}$`.** The genesis record's `prev_record_hash` is 64 zeros, which matches the same pattern, so no special case is needed. The schema checks shape only. Chain integrity (recomputing hashes) is a job for `verify_log()`, because JSON Schema can't compute SHA-256.
- **`subagent_result` is `null` or an object, with only the fields the coordinator reads required.** For sanctions that is `verdict` and `confidence`. For enrichment it is `risk_score` and `confidence`. The rest is model output and may carry extra keys, so it isn't closed. `null` means the subagent was never called because boundary validation failed first.
- **Sanctions `verdict` allows `UNKNOWN` as well as `MATCH`, `CLEAR` and `INVALID`.** The tool's JSON schema doesn't list `UNKNOWN`, but the degraded paths (unavailable, refused, unparseable) return it, so it appears in real records.
- **Verdict casing differs from the eval contract, and the audit format is unchanged.** Audit records hold the subagent's raw output (`MATCH`). The eval contract uses the coordinator's normalised lowercase form (`match`). Each schema matches what its producer writes. Changing the audit format would break the hash chain for existing records, so comparison goes through the mapping table below instead.
- **Routing is tied to the decision.** `review` implies `human_review_required`, and `clear` or `block` implies `auto_decided`. This encodes the coordinator's rule that only review needs a human, and it catches records where the two disagree.
- **`timestamp` is declared `format: date-time` and the test enforces it.** Validators ignore `format` unless given a format checker. The test passes jsonschema's `FormatChecker`, which needs `rfc3339-validator` for `date-time`. It also parses the value with `datetime.fromisoformat` as a backstop, in case the checker is ever silently unavailable. Consumers should do the same.
- **`sanctions_screening` and `enrichment` are closed (`additionalProperties: false`).** Each is written out in full rather than sharing a `$ref`, because in 2020-12 a closed shared shape rejects properties added beside the `$ref`. The duplication is small. I checked the existing records first: they carry exactly the four keys `boundary_validation_passed`, `boundary_rejection_reason`, `subagent_called` and `subagent_result`, so no extra keys had to be added. `subagent_result` itself stays open because it is model output.

## Eval test case

- **Required fields match the module docstring.** They are `id`, `category`, `description`, the three transaction inputs, and `expected_decision`. The rest are optional because they are optional in the code.
- **`amount` is any number, with no `minimum`.** Test inputs must be allowed to be invalid, otherwise negative tests couldn't exist. The eval contract describes inputs we feed the system, including bad ones, so it can't encode the rules the system is supposed to enforce. `edge_boundary_amount_negative` (`-500`) is one such case. It is also marked a known limitation because the system doesn't reject it yet. Whether the system rejects a value is checked by `expected_decision`, not by the schema.
- **`sender_name` and `customer_id` are unconstrained strings.** The injection cases deliberately put malicious text in these fields.
- **`id` matches `^[a-z0-9_]+$`.** The ids are used as stable keys, so a conservative character set keeps them safe to use in filenames and reports. Uniqueness can't be expressed per-case, so the test checks it.
- **Verdict enums are lowercase and include `unknown` and `invalid`.** They follow the coordinator's normalised values.

## Verdict mapping

Sibling repos should compare audit and eval values through `normalize_verdict(field, raw)` in `contracts/verdicts.py`. It lowercases the value and maps anything outside the known set to `unknown`, which matches `coordinator._normalize_sanctions_verdict()` and `_normalize_risk_score()`. A test checks that it agrees with both. An unrecognised `field` raises `ValueError`.

`field="sanctions_verdict"`:

| Audit record (`subagent_result.verdict`) | Eval case (`expected_sanctions_verdict`) | Canonical |
|---|---|---|
| `MATCH` | `match` | `match` |
| `CLEAR` | `clear` | `clear` |
| `INVALID` | `invalid` | `invalid` |
| `UNKNOWN` | `unknown` | `unknown` |
| anything else, or `null` | n/a | `unknown` |

`field="risk_score"`: the audit record's `subagent_result.risk_score` and the eval's `expected_risk_score` are both already lowercase (`low`, `medium`, `high`, `invalid`, `unknown`), so they pass through unchanged. Anything else, or `null`, becomes `unknown`.

Not every enrichment risk score is a subagent output: the coordinator also treats a failed boundary check as `invalid` without calling the subagent. In that case the audit record has `subagent_result: null`, so compare `decision` and the boundary fields instead.

## Agent identity (new design, status: draft)

- **It says who an agent is, not what it may do.** The fields are `agent_id`, `name`, `version`, `owner`, `purpose` and `autonomy`. Permissions live only in policy rules. Putting an allow-list in both places would let them disagree.
- **`agent_id` is lowercase kebab-case and is the join key.** Policy rules and audit events refer to agents by it. It should never be reused after an agent is retired.
- **`version` is semver for the agent's behaviour.** A prompt, model or tool change is a behaviour change, so an audit trail can say which version acted.
- **`owner` is required.** Every agent has an accountable party.
- **`autonomy` is `human_in_the_loop`, `human_on_the_loop` or `autonomous`.** It reuses the vocabulary from `docs/governance-mapping.md`. The coordinator's auto-block is the on-the-loop case.
- **`model` is optional.** Not every agent has an LLM behind it.

Example, the sanctions subagent:

```json
{
  "agent_id": "sanctions-screening",
  "name": "Sanctions screening subagent",
  "version": "1.0.0",
  "owner": "financial-crime-engineering",
  "purpose": "Screen a sender name against the sanctions watchlist.",
  "autonomy": "human_on_the_loop"
}
```

The owner and version values above are illustrative placeholders, not taken from the repo.

## Tool-call policy rule (new design, status: draft)

- **One rule is one agent, one tool and one effect, with optional conditions.** Small rules are easy to review, diff and test.
- **Evaluation semantics are stated in the schema description.** No matching rule means deny. Any matching `deny` wins. Otherwise `require_human_approval` beats `allow`. Default-deny and deny-overrides are the conservative choices, and putting them in the contract keeps sibling repos from implementing different semantics.
- **`effect` has three values: `allow`, `deny` and `require_human_approval`.** The third makes the human-in-the-loop path explicit rather than something a caller improvises.
- **`*` is the only wildcard, for `agent_id` and `tool`.** It's a bracket class in the patterns (`[*]`), so there are no regex escapes to get wrong. There are no globs or partial wildcards, which keeps matching unambiguous.
- **`conditions` is a list of `{argument, operator, value}`, combined with AND.** The operators are a closed set: `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`, `not_in` and `matches`. This is data, not code, so rules can be audited and can't carry executable logic. OR is expressed as separate rules.
- **`value` is untyped.** Its type depends on the argument tested, and a schema can't know that. The evaluator is responsible for that check.

Examples:

```json
{
  "rule_id": "sanctions_may_check_list",
  "description": "Sanctions subagent may call its one tool.",
  "agent_id": "sanctions-screening",
  "tool": "check_sanctions_list",
  "effect": "allow"
}
```

```json
{
  "rule_id": "large_transfer_needs_approval",
  "description": "Any transfer over 10000 needs a human.",
  "agent_id": "*",
  "tool": "initiate_transfer",
  "effect": "require_human_approval",
  "conditions": [{ "argument": "amount", "operator": "gt", "value": 10000 }]
}
```

`initiate_transfer` is hypothetical and doesn't exist in this repo. The two new schemas are proposals for the sibling repos to react to. This repo has no code that produces or consumes them yet, so the test checks only that these examples validate.
