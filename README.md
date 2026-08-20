# Secure Agentic Banking

A multi-agent AML/sanctions transaction-triage system, red-teamed by its
own author — with the vulnerabilities found, the fixes applied, and the
metrics that verify them.

A coordinator agent takes a transaction (sender name, customer ID,
amount), delegates screening and enrichment to two single-tool subagents,
and combines their results into a `clear` / `review` / `block` decision.
Every decision is logged to a tamper-evident audit trail; anything the
system can't safely auto-decide is routed to a human review queue with an
explicit, named reason.

**Key concepts demonstrated:**

- **Least-privilege agents** — each subagent has exactly one tool, enforced structurally (see [How it works](#how-it-works))
- **Prompt-injection defense** — three real vulnerabilities found and fixed, in progressive layers ([`docs/security-findings.md`](docs/security-findings.md))
- **Structured decision channels** — schema-constrained verdicts, never parsed from prose
- **Red-team evals** — adversarial test cases with scored, repeatable metrics ([`evals/`](evals))
- **Audit trail** — hash-chained, tamper-evident logging, tested by actually tampering with it ([`agents/audit_log.py`](agents/audit_log.py))
- **Regulatory mapping** — controls mapped to the EU AI Act and NIST AI RMF, gaps stated explicitly ([`docs/governance-mapping.md`](docs/governance-mapping.md))
- **Human-in-the-loop routing** — confidence/risk/ambiguity-based rules, not auto-decide-all or random sampling ([`agents/routing_rules.py`](agents/routing_rules.py))

**Three real prompt-injection vulnerabilities were found by attacking this
system's own coordinator, in progressive layers — each fix exposed the
next problem:**

| Finding | Exploited | Fix |
|---|---|---|
| Decision-channel contamination | The coordinator's keyword-parsing misread a subagent's own *honest* refusal of an injected instruction | Structured outputs — a schema-constrained verdict field, separate from free-text explanation |
| Tool-boundary input pollution | The decision channel was clean, but the *tool call argument* underneath it wasn't — a polluted string reached a lookup key | Two-layer input validation (coordinator boundary check + tool-level backstop) |
| Human-oversight enforcement gap | Routing to "review" was a label with no workflow behind it — nothing confirmed a human ever looked | A confidence/risk/ambiguity-based rule engine plus a real review queue, resolvable with a recorded reviewer decision |

Full writeup: [`docs/security-findings.md`](docs/security-findings.md).
Mapped to EU AI Act high-risk obligations and the NIST AI RMF — including
an explicit accounting of what's partial and what production would add:
[`docs/governance-mapping.md`](docs/governance-mapping.md).

**Eval harness, last full run:** 11 labeled test cases (clean transactions,
confirmed sanctions matches, the three injection attacks, and validation
edge cases) — **100% decision accuracy, 100% injection-resistance rate,
0% unsafe-action rate.** Run it yourself: `python evals/run_evals.py`.

---

## Architecture

```
                         Transaction (sender_name, customer_id, amount)
                                          |
                                          v
                          +----------------------------+
                          |        coordinator.py        |
                          |  (delegates + combines only  |
                          |   -- no lookup capability     |
                          |   of its own)                |
                          +---------------+---------------+
                                          |
             input validation (validation.py) -- boundary check
             before either subagent is ever called
                                          |
              +---------------------------+---------------------------+
              |                                                       |
              v                                                       v
   +----------------------+                              +--------------------------+
   | sanctions_screening.py |                              |      enrichment.py        |
   |  ONE tool:             |                              |  ONE tool:                 |
   |  check_sanctions_list   |                              |  get_customer_risk_profile |
   |  structured verdict:    |                              |  structured risk_score:    |
   |  MATCH / CLEAR / INVALID|                              |  low / medium / high /     |
   |                         |                              |  unknown / invalid         |
   +------------+-----------+                              +-------------+--------------+
                |                                                        |
                +------------------------+-------------------------------+
                                          v
                     routing_rules.py -- 6 named, independently
                     configurable rules decide: clear / review / block
                                          |
                     +--------------------+--------------------+
                     v                                          v
        audit_log.py (hash-chained,                 review_queue.py (pending/
        tamper-evident JSONL, every                  resolved, only when a human
        transaction, no exceptions)                  is actually needed)
```

Each subagent's `tools` list contains exactly one entry — that's not a
convention, it's the mechanism: the Anthropic API can only emit a tool call
for a tool it was given, so neither subagent has any capability beyond its
one lookup, structurally, independent of what it's told or tricked into
attempting.

## Quick start

```bash
git clone <this-repo-url>
cd secure-agentic-banking

python -m venv venv
source venv/Scripts/activate      # PowerShell: venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Verify the setup (package installed, key present, one live "reply with OK"
call, no key ever printed):

```bash
python check_setup.py
```

Run one transaction through the full pipeline:

```bash
cd agents
python run_coordinator.py
```

Run the adversarial attacks or the scored eval suite:

```bash
python agents/attacks.py        # 3 hand-run adversarial transactions
python evals/run_evals.py       # 11 labeled cases, scored + summary metrics
```

Check the audit trail and the human review queue:

```bash
python agents/audit_log.py      # verifies the hash chain, reports tampering if any
python agents/review_queue.py   # lists everything currently pending human review
```

## How it works

- **Coordinator-worker pattern.** `coordinator.py` holds no sanctions list,
  no risk table, and no lookup tool of its own — it only knows how to ask
  each subagent for an outcome and combine two structured answers. This is
  least privilege applied one level up: even a compromised coordinator
  couldn't fabricate a verdict, because it has no tool to fabricate one
  with.
- **Structured decision channels.** Each subagent's final answer is
  constrained by `output_config.format` to a fixed schema — a `verdict` /
  `risk_score` enum plus a `confidence` field, separate from a free-text
  `explanation`. The coordinator reads only the constrained fields, never
  the prose — closing the exact bug class in Finding 1 of the security
  writeup.
- **Two-layer input validation.** `validation.py`'s patterns are checked
  once at the coordinator boundary (before a subagent is ever called) and
  again inside each tool function (a backstop in case anything bypasses
  the first layer). Neither layer trusts the other.
- **Confidence/risk/ambiguity-based routing.** `routing_rules.py` defines
  six independently-named, independently-toggleable rules — low
  confidence, conflicting signals between the two subagents, validation
  rejection, an unresolved lookup, an amount over a configurable
  threshold, and an elevated risk tier — any one of which routes a
  transaction to human review instead of auto-deciding it. A confirmed
  sanctions match still auto-blocks (the one safe direction to auto-decide
  in); everything else has to earn its way to `clear`.
- **Tamper-evident audit logging.** `audit_log.py` writes one hash-chained
  JSON record per transaction, capturing the full input, both subagents'
  complete structured output, any boundary rejection, the decision,
  confidence, and routing. The tamper-evidence claim was tested, not just
  implemented: a record was edited in place and the verifier correctly
  named the exact record and field that changed.
- **A real review queue.** Routing a transaction to `review` writes it to
  `audit/review_queue/pending/`; resolving it records the reviewer's
  identity, decision, and notes, and moves it to `resolved/` — closing the
  gap where "routed to review" was previously just a label.
- **Production hygiene.** Structured JSON logging with levels (`logging_config.py`),
  bounded retries with exponential backoff around every subagent API call
  that degrade to a safe `review`-routed result rather than crash
  (`api_utils.py`), and centralized, environment-overridable configuration
  for every threshold and model setting (`config/settings.py`).

## Security & governance

The full three-finding narrative — what was attacked, what it exploited,
how the undefended system responded, and why each fix is the right
principle rather than a patch — is in
[`docs/security-findings.md`](docs/security-findings.md). It's written as
a progression on purpose: each fix was correct as far as it went, and each
one is what surfaced the next problem.

[`docs/governance-mapping.md`](docs/governance-mapping.md) maps every
control in this system — input validation, structured decision channels,
least-privilege scoping, the eval harness, audit logging, human-in-the-loop
routing — to specific EU AI Act high-risk obligations and NIST AI RMF
functions, with an explicit "Honest assessment" on each one stating what's
fully addressed versus a demo-grade placeholder. It also includes a
dedicated section distinguishing the audit log's tamper-*evidence* from
tamper-*proofing*, and what closing that gap would actually require in
production.

## Testing & evals

`evals/test_cases.py` defines 11 labeled cases across four categories —
clean transactions, confirmed sanctions matches, the three prompt-injection
attacks, and validation/decision edge cases (a non-adversarial malformed
ID, a well-formed-but-unknown customer ID, a one-character near-miss on a
sanctioned name, a negative amount) — each with an expected decision and,
where applicable, the expected sanctions verdict and risk score
separately, so a passing decision can be told apart from a passing
decision reached for the wrong reason.

`evals/run_evals.py` runs every case against the live coordinator and
reports:

- **Overall accuracy** — decision matches the labeled expectation, across
  all cases.
- **Injection-resistance rate** — of the injection cases specifically, the
  fraction that did *not* result in `clear`.
- **Unsafe-action rate** — of all cases that should *not* clear, the
  fraction that were nonetheless cleared. This is the single most
  important number in the report.
- **Per-category breakdown.**

Last full run: **100% accuracy, 100% injection-resistance, 0%
unsafe-action rate**, 4/4 categories at 100%. Because the subagents call a
live model, expect occasional single-run non-determinism — one run during
development returned a flaky wrong value on a single case, confirmed
non-reproducible by three immediate retries against the same input. That's
exactly the reason this project has a hash-chained audit log and a
scored, repeatable harness rather than relying on "it worked when I tried
it."

## Project structure

```
agents/
  coordinator.py            orchestrates both subagents, decides, routes, logs
  sanctions_screening.py    single-tool subagent: check_sanctions_list
  enrichment.py             single-tool subagent: get_customer_risk_profile
  validation.py             input-shape patterns, enforced at two layers
  routing_rules.py          the 6 named human-review routing rules
  review_queue.py           file-based pending/resolved human review queue
  audit_log.py              hash-chained, tamper-evident JSONL audit trail
  api_utils.py              retry/backoff wrapper for subagent API calls
  logging_config.py         shared structured (JSON) logging setup
  attacks.py                3 hand-run adversarial transactions
  run_coordinator.py        sample runner
  run_sanctions_screening.py / run_enrichment.py   per-subagent sample runners

config/
  settings.py                every threshold/model setting, env-overridable

evals/
  test_cases.py              11 labeled test cases across 4 categories
  run_evals.py                scored harness: accuracy, injection-resistance,
                               unsafe-action rate, per-category breakdown

docs/
  security-findings.md        the 3-finding red-team narrative, before/after
  governance-mapping.md       controls mapped to EU AI Act / NIST AI RMF

audit/
  transactions.jsonl          the audit trail (gitignored -- runtime data)
  review_queue/                pending/ and resolved/ human review items

check_setup.py                environment sanity check (key present, live call)
requirements.txt              direct dependencies only
```

## Limitations and what production would add

Stated directly rather than left implicit — the full version of each of
these lives in `docs/governance-mapping.md`:

- **No confirmed regulatory classification.** Whether a production
  descendant of this system is "high-risk" under the EU AI Act is a legal
  determination this project doesn't make on its own behalf.
- **Input validation is a demo-appropriate placeholder.** The name pattern
  is ASCII-only and would reject legitimate international names; the
  transaction amount has no sanity check (negative or zero amounts pass
  through unexamined).
- **The review queue has no enforced SLA.** An item can sit in
  `pending/` indefinitely with nothing detecting or escalating it.
- **The audit log is tamper-evident, not tamper-proof.** The hash chain
  detects edits, reordering, or deletion of existing records; it cannot
  detect wholesale file replacement by someone who controls both the data
  and the verification code. Closing that needs write-once storage and
  external hash anchoring outside this codebase — see
  `docs/governance-mapping.md`'s dedicated section on this.
- **No CI integration yet.** The eval suite and the attack script are run
  manually; nothing currently gates a merge on them.
- **The eval suite is 11 hand-curated cases, run once per invocation.**
  Real statistical confidence would need substantially broader coverage
  and repeated trials per case, given the non-determinism noted above.
