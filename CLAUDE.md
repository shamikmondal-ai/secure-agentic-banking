# CLAUDE.md

## Project context (as of 2026-10-13)

**What it is:** Multi-agent AML/sanctions transaction-triage pipeline, red-teamed by its own author, demonstrating safe autonomous-agent patterns for banking. `coordinator.py` delegates to two single-tool subagents (`sanctions_screening.py`, `enrichment.py`), combines structured verdicts into clear/review/block, routes via `routing_rules.py` (6 named rules), logs via `audit_log.py` (hash-chained JSONL), queues human review via `review_queue.py`. `validation.py` enforces identifier shape at two layers (coordinator boundary + tool backstop). `REFERENCE-ARCHITECTURE.md` generalizes this into a bank-wide control-plane blueprint (registry, gateways, policy engine, kill switch) — explicit about what's demonstrated in code vs. blueprint-only.

**Built & working:** Full pipeline runs end-to-end. Three real prompt-injection vulnerabilities found and fixed in progressive layers (`docs/security-findings.md`): decision-channel contamination → structured outputs; tool-boundary input pollution → two-layer validation; human-oversight gap → real review queue. Audit tamper-evidence verified by an actual tampering test, not just asserted.

**Tests/evals:** `evals/test_cases.py` — 11 labeled cases (clean, sanctions match, 3 injection attacks, edge cases) run via `evals/run_evals.py` against the live coordinator. Last run: 100% accuracy, 100% injection-resistance, 0% unsafe-action rate. `agents/attacks.py` has 3 hand-run adversarial transactions. No CI integration — evals/attacks run manually only.

**Known limitations (stated in docs, not hidden gaps):**
- No confirmed EU AI Act regulatory classification (legal determination, out of scope)
- `validation.py` name pattern is ASCII-only — rejects legitimate international names
- Transaction `amount` is never validated — negative/zero amounts pass through unexamined
- Review queue (`review_queue.py`) has no enforced SLA/escalation
- Audit log is tamper-*evident*, not tamper-*proof* (can't detect wholesale file replacement)
- Eval suite is 11 hand-curated cases, run once per invocation; known single-run non-determinism observed

**Docs/code consistency:** Checked 2026-10-13 — README and REFERENCE-ARCHITECTURE.md claims (validation patterns, 6 routing rules, no CI, ASCII-only limitation) all match current code. No mismatches found.
