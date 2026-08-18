# Governance Mapping: Controls vs. EU AI Act & NIST AI RMF

## Scope and disclaimer

This document maps the concrete technical controls built in this codebase
to requirements from the EU AI Act's high-risk AI system obligations
(Title III, Chapter 2, roughly Articles 9-15) and the NIST AI Risk
Management Framework's four functions (Govern, Map, Measure, Manage).

Two things this document is **not**:

- **Not a legal determination.** Whether this system (or a production
  descendant of it) is classified as "high-risk" under the EU AI Act's
  Annex III is a legal question that depends on exact deployment context,
  and has been refined through implementing guidance since the Act's
  adoption. I'm reasonably confident in the substance of Articles 9-15
  below, but article numbers and precise text should be verified against
  the current official text before this document is used in any real
  compliance filing. Get this confirmed by qualified counsel — don't treat
  this file as that confirmation.
- **Not a claim of compliance.** This maps *controls that exist in code
  today* to *requirements they partially or fully address*. Several
  mappings below are explicitly partial, and the "System-level gaps"
  section at the end lists what no control here addresses at all.

Re-verify this mapping whenever the underlying code changes — it describes
the system as of the commits through `feat/audit-governance`, not a
permanent property of the codebase.

---

## Control 1: Input validation

**What it is:** `agents/validation.py`'s regex patterns, enforced twice —
once in `coordinator.py` before either subagent is called, once inside
`check_sanctions_list()` / `get_customer_risk_profile()` as a backstop.

**EU AI Act:**
- **Article 9 (risk management system)** — this is the direct fit. The
  Act requires identifying and mitigating "reasonably foreseeable misuse."
  Prompt injection via a malformed identifier is exactly that, and this
  control is the documented mitigation (see `docs/security-findings.md`,
  Finding 3).
- **Article 15 (accuracy, robustness, cybersecurity)** — specifically the
  requirement for resilience against attempts to alter system behavior by
  exploiting vulnerabilities. A shape-check on untrusted input before it
  reaches a model or a tool argument is a textbook robustness control.

**NIST AI RMF:**
- **Map** — identifying malformed/adversarial input as a named risk for
  this specific use case.
- **Manage** — the implemented response to that mapped risk.

**Honest assessment: partial.** The patterns
(`^[A-Za-z][A-Za-z' -]{0,79}$` for names, `^CUST-\d{4}$` for customer IDs)
are demo-appropriate placeholders, not production-ready validation. Two
concrete gaps: (1) the name pattern is ASCII-only and would incorrectly
reject legitimate names with diacritics, non-Latin scripts, or other
real-world name formats — a production version needs internationalized
validation, not a blunt allowlist; (2) `amount` has **no validation at
all** (see the `edge_boundary_amount_negative` case in
`evals/test_cases.py`) — this control covers two of the three transaction
fields, not all of them.

---

## Control 2: Structured decision channels

**What it is:** `output_config.format` in `sanctions_screening.py` /
`enrichment.py`, constraining `verdict` / `risk_score` / `confidence` to a
fixed schema, separate from free-text `explanation`. The coordinator reads
only the constrained fields.

**EU AI Act:**
- **Article 13 (transparency and provision of information)** — a human
  overseeing this system needs an intelligible account of *what* the
  system decided, structurally separated from *how it explained itself*.
  This is what makes the decision auditable rather than just narrated.
- **Article 12 (record-keeping)**, indirectly — structured fields are what
  make the audit log (Control 5) machine-parseable rather than a transcript
  someone has to read and interpret by hand.

**NIST AI RMF:**
- **Measure** — a schema-constrained field is what makes automated grading
  in the eval harness (Control 4) possible at all. Free text can't be
  reliably graded at scale; an enum can.

**Honest assessment: partial, and the boundary matters.** This control
fixes the *decision-channel* integrity problem documented as Finding 2 in
`docs/security-findings.md` — a subagent's stated verdict can no longer be
misread by keyword-scanning its own prose. It does **not**, by itself,
guarantee the verdict is *correct*: Finding 3 in the same document shows a
subagent producing a perfectly schema-valid, internally consistent, and
still-wrong answer because the underlying tool call was fed polluted input.
Structured output disciplines the shape of the answer, not its accuracy —
that's why Control 1 had to be added separately. Also worth stating
plainly: `confidence` is model self-reported and has already been observed
being wrong-and-confident in this system (a `"low"` risk score reported
with `confidence: "high"` — logged in `audit/transactions.jsonl` during
testing). Treat it as evidence to review, not a correctness guarantee.

---

## Control 3: Least-privilege agent scoping

**What it is:** each subagent's `tools` list contains exactly one tool,
enforced by what the Anthropic API will accept as a valid `tool_use` call,
plus a redundant dispatch-time check in each subagent's loop.

**EU AI Act:**
- **Article 15 (accuracy, robustness, cybersecurity)** — limiting each
  component's capability surface directly bounds the consequence of a
  successful manipulation, which is the practical substance of
  "resilience against attempts to exploit vulnerabilities."
- **Article 9 (risk management system)**, as a designed mitigation for the
  general risk of excessive agentic autonomy.

**NIST AI RMF:**
- **Govern** — an architectural policy decision (minimum necessary
  capability per component), the same principle NIST's broader access-
  control guidance applies to conventional IT systems.
- **Manage** — reduces the severity of a realized risk, independent of
  whether that risk is prevented elsewhere.

**Honest assessment: fully addresses what it targets, which is narrower
than it sounds.** This control is genuinely structural, not just
policy-on-paper — verified directly in the red-team exercise, where a
fully manipulated enrichment subagent still could not take or claim an
unauthorized action because no such tool was ever available to it. But
"can't take an unauthorized *action*" is a different guarantee from "can't
produce a wrong or misleading *answer*" — Finding 2 in the security
findings doc shows a subagent being completely un-compromised on the
action front while the *decision* was still nearly corrupted by a harness
bug elsewhere. Least privilege is necessary and holds firmly, but it's one
of three complementary controls, not a complete answer to agentic risk on
its own.

---

## Control 4: Eval harness

**What it is:** `evals/test_cases.py` (11 labeled cases across four
categories) and `evals/run_evals.py` (overall accuracy, injection-
resistance rate, unsafe-action rate, per-category breakdown).

**EU AI Act:**
- **Article 9 (risk management system)** — the Act requires testing
  procedures throughout the system's lifecycle, not just at initial
  deployment.
- **Article 15 (accuracy, robustness, cybersecurity)** — accuracy claims
  need to be demonstrated against defined metrics, not asserted.
- **Annex IV (technical documentation)** — typically expects disclosure of
  testing methodology and results as part of the required documentation
  package.

**NIST AI RMF:**
- **Measure** — this is the cleanest match in this entire document. This
  *is* TEVV (test, evaluation, verification, validation) as NIST defines
  it. The injection-resistance-rate metric specifically operationalizes a
  risk NIST's Generative AI Profile (AI 600-1) names explicitly: prompt
  injection.

**Honest assessment: partial, and meaningfully so.** Eleven hand-curated
cases is a real start, not a statistically powered test suite — a 100%
pass rate on 11 cases is a much weaker claim than a 100% pass rate on 500.
More importantly, **the harness runs each case once, against a
non-deterministic system.** We observed this directly: a manual retest of
one case produced a flaky wrong answer that an earlier full harness run
didn't happen to catch. A single clean run establishes "worked on this
run," not a stable reliability rate. Production needs: (1) substantially
broader and more diverse case coverage, (2) repeated trials per case with
reported variance — not a single pass/fail per case, (3) integration into
CI so every code change re-runs the suite automatically, and (4) a stated
acceptance policy (e.g., "unsafe-action rate must be 0% across N trials
before merge") that gates releases rather than just informing them.

---

## Control 5: Audit logging

**What it is:** `agents/audit_log.py` — every transaction produces a
timestamped, hash-chained JSONL record capturing the full input, both
subagents' complete structured output (or the boundary-rejection reason if
a subagent was never called), the decision, confidence, and routing.

**EU AI Act:**
- **Article 12 (record-keeping)** — this is the most direct match to any
  single requirement in the Act, of any control in this document. The Act
  asks for automatic logging sufficient to reconstruct the system's
  operation across its lifecycle; that is close to a literal description
  of what this control does.
- **Article 13 (transparency)**, supportively — the record is intelligible
  to a human reader, not just a machine.

**NIST AI RMF:**
- **Govern** — accountability: what happened, traceably.
- **Manage** — incident response depends on exactly this kind of record
  existing *before* an incident, not being reconstructed after one.

**Honest assessment: the strongest control in this system, with an
explicitly demonstrated limit** — see the dedicated section immediately
below for what that limit is and what closes it. Briefly: I didn't just
implement the hash chain, I tampered with a written record's content and
confirmed `verify_log()` catches it and identifies exactly which record and
field changed, then restored the original and confirmed it re-validates
clean. Additionally, independent of the tamper-evidence question: the log
currently lives unencrypted on local disk with no retention policy and no
access control of its own — anyone with filesystem access can read every
transaction's full detail, including any attacker-controlled text a
boundary check didn't already strip.

---

## Limitations and path to production: audit log tamper-evidence

This is worth its own section rather than a caveat inside Control 5,
because the distinction it draws — tamper-*evident* vs. tamper-*proof* — is
exactly the kind of nuance a model-risk review or regulator will press on,
and glossing it into a single paragraph invites overclaiming.

**What the hash chain actually proves.** Every record stores the SHA-256
hash of the record before it (`prev_record_hash`) and a hash of its own
content (`record_hash`). `verify_log()` replays the chain from the top and
recomputes both. This detects, with certainty, three classes of tampering
against records already written to the file:

- **Editing** any field in a past record (its `record_hash` no longer
  matches its content).
- **Reordering** records (the `prev_record_hash` linkage breaks).
- **Deleting** a record from the middle or end of the chain (the following
  record's `prev_record_hash` points to a hash that's no longer there).

I demonstrated this directly rather than asserting it: I edited a written
record's `risk_score` in place, left its stored `record_hash` untouched,
and `verify_log()` immediately named that exact record and field as
altered. That's tamper-**evidence** — a real, checkable property, not a
description of intent.

**What it does not prove.** A hash chain is a statement about internal
consistency of the file's contents, given that the file and the
verification code are both trustworthy. It cannot detect:

- Someone with filesystem write access **truncating the file entirely**
  and starting a fresh chain from a new genesis hash — the new chain would
  verify as perfectly "intact," because internal consistency is all
  `verify_log()` checks. There would be no way, from inside this codebase
  alone, to tell that the file was replaced today rather than having
  existed since the first transaction.
- Someone with write access to **both the data and this verification
  code** modifying them together — e.g. changing `audit_log.py` itself to
  accept a different genesis hash, or to skip verification of a specific
  record.

Put plainly: this is the right mechanism, but a mechanism alone is not
custody. Tamper-evidence that only the system being audited can check is
tamper-evidence the system being audited can also quietly disable.

**What production would add, to pair the mechanism with real custody:**

- **Write-once storage.** Write the log (or mirror it) to storage the
  application itself has no delete or overwrite permission on — S3 Object
  Lock in compliance mode, a WORM (write-once-read-many) volume, or an
  append-only database with no `UPDATE`/`DELETE` grant for the application's
  own credentials. This moves "can this record be changed" from a property
  the application promises to a property the storage layer enforces,
  independent of the application's own code.
- **Periodic external anchoring.** Regularly publish the latest
  `record_hash` to a system the application cannot itself write to — a
  separate, independently-operated service, a different team's log
  aggregator, even a scheduled export to cold storage with its own access
  controls. This bounds how far back a wholesale-replacement attack could
  reach: anything anchored externally before the tampering can't be
  silently rewritten without the rewrite being visible against the anchor.
- **Separation of write credentials.** The identity that runs transaction
  processing should not be the same identity permitted to modify or delete
  historical audit records. If the application's own credentials can
  rewrite the log, the log's integrity is only as strong as that one
  credential's secrecy. A regulator or auditor should be able to name a
  *different* party who holds deletion rights, and confirm that party
  isn't the system being audited.

None of these three exist in this codebase today. What exists is the right
mechanism, unpaired with the right custody — closing that gap is
infrastructure work outside the application, not a code change inside it.

---

## Control 6: Human-in-the-loop routing

**What it is:** the `decision` field's `review` value, plus the explicit
`routing` field (`auto_decided` vs. `human_review_required`) added
alongside audit logging.

**EU AI Act:**
- **Article 14 (human oversight)** — the direct, central match. The Act
  requires that high-risk AI systems be designed so a human can
  effectively oversee them, including the ability to decide not to use, or
  to override, the system's output. Routing ambiguous or dangerous cases
  to a human rather than auto-deciding them is exactly the mechanism this
  article asks for.

**NIST AI RMF:**
- **Govern** — defining who is accountable when a case is routed for
  review.
- **Manage** — the actual risk-response mechanism for cases the automated
  path can't safely resolve.

**Honest assessment: this is the largest gap in the system relative to
what its own regulatory citation requires, and it's worth stating
plainly.** `routing: "human_review_required"` is a **label describing
intent**, not an **enforced workflow**. There is no case queue, no forced
sign-off, no SLA, and no record of who reviewed a case, what they decided,
or why. A transaction routed to `review` today can simply never be looked
at by anyone, and nothing in the system would know that happened or record
it. Article 14 is not satisfied by a system that politely asks for human
oversight — it requires oversight that actually occurs and is
demonstrable. Production needs a real case-management layer: a queue,
assignment, a required human decision captured with the same audit rigor
as the machine's (who, when, what, why), and a way to detect and escalate
cases that sit unreviewed past a defined SLA.

---

## Summary table

| Control | EU AI Act | NIST AI RMF | Status |
|---|---|---|---|
| Input validation | Art. 9 (risk mgmt), Art. 15 (robustness) | Map, Manage | Partial — ASCII-only names, no amount validation |
| Structured decision channels | Art. 13 (transparency), Art. 12 (records) | Measure | Partial — disciplines shape, not correctness |
| Least-privilege agent scoping | Art. 15 (robustness), Art. 9 (risk mgmt) | Govern, Manage | Fully addresses action-level risk; doesn't cover decision-correctness risk |
| Eval harness | Art. 9 (testing), Art. 15 (accuracy), Annex IV (docs) | Measure | Partial — small sample, single-trial, not yet CI-gated |
| Audit logging | Art. 12 (records), Art. 13 (transparency) | Govern, Manage | Strongest control; tamper-evident, not tamper-proof; no external custody |
| Human-in-the-loop routing | Art. 14 (human oversight) | Govern, Manage | Weakest control; intent without enforcement |

## System-level gaps (not owned by any single control)

- **No confirmed regulatory classification.** Whether this system is
  "high-risk" under the Act hasn't been determined by counsel — this
  document assumes it might be, to map conservatively.
- **No conformity assessment, CE marking, or EU database registration** —
  required for certain high-risk categories, not attempted here.
- **No Annex IV technical documentation package** — this repo's docs are
  informative, not the formal documentation artifact the Act specifies.
- **No continuous monitoring program.** Every control above reflects a
  point-in-time exercise (one red-team session, one eval run, one
  tamper-evidence demonstration), not an ongoing process re-run on a
  schedule or on every change. Both frameworks expect the latter.
- **No stated risk tolerance or acceptance criteria** (NIST Govern) — there
  is no documented answer to "what unsafe-action rate would block a
  release," which means the eval harness currently *informs* decisions
  without *gating* them.
