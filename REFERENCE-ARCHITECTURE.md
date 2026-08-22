# Safely Deploying Autonomous AI Agents in a Bank: A Reference Architecture

A vendor-neutral blueprint for how a bank runs autonomous AI agents — across
credit, KYC, payments, and ops — as a governed fleet rather than a
collection of one-off projects. It generalizes the patterns proven in a
working implementation ([`shamikmondal-ai/secure-agentic-banking`](README.md))
into a control-plane architecture, states which parts of that architecture
are demonstrated in code today versus which remain blueprint, and maps the
whole thing to the two frameworks a bank's model-risk and AI-governance
functions will actually ask about.

This document does not recommend a product, a cloud, or a specific
orchestration framework. Every capability below is described as a function
a bank needs, not a SKU.

---

## 1. The problem: autonomy without a control plane doesn't scale

A single well-built agent is a manageable risk. A bank running one is not
the interesting case — the interesting case is the fifth one, built by a
different team, calling a different set of tools, against a different
core system.

That trajectory is already underway: a credit-decisioning agent that pulls
bureau data and drafts an adverse-action notice, a KYC agent that resolves
entity matches and flags beneficial-ownership ambiguity, a payments agent
that screens and routes transactions, an ops agent that triages fraud
alerts or reconciles breaks. Each is individually plausible. Each also has
its own idea of what "high risk" means, its own ad hoc logging format, its
own definition of when a human needs to look at something, and — usually —
no way for a CISO, model-risk officer, or auditor to answer, in one place,
*what can our agents do right now, and can we stop them.*

Three properties of agents make this worse than the equivalent sprawl of
traditional services:

- **The action surface is dynamic.** A microservice's capabilities are
  fixed by its deployed code. An agent's effective capabilities are
  whatever tools it's been wired to plus whatever a model decides to do
  with them in a given run — a much larger and less enumerable surface,
  and one that can change by editing a tool list rather than by a
  reviewed code change.
- **The failure mode is non-deterministic.** The same input can produce a
  different output on retry. A control that "worked" on one run is weak
  evidence it will work on the next one — this has been observed directly
  in the reference implementation (see [§5](#5-reference-implementation)),
  not asserted hypothetically.
- **The cost of failure is asymmetric and regulatory, not just
  operational.** A cleared sanctions hit or a wrongly-approved credit
  decision isn't a bug ticket — it's a matter regulators, examiners, and
  in some jurisdictions criminal liability already have a defined stance
  on, independent of whether a human or an agent made the call.

The diagnosis this document works from: the risk isn't that any single
agent is unsafe. It's that safety becomes an artisanal, per-team practice
instead of an enforced, bank-wide property. That's a control-plane
problem — an architecture question — not a prompt-engineering one.

---

## 2. The control-plane concept

The core architectural move is separating **what agents do** (the
execution plane: an agent's task loop — plan, call a model, call tools,
combine results) from **what governs, observes, and can halt them** (the
control plane). Every capability in §3 is an enforcement point threaded
into that separation, not a bolt-on feature.

```
                         User / upstream system / trigger
                                        |
                                        v
                              +-------------------+
                              |       Agent        |
                              |  (task loop: plan,  |
                              |  call model, call    |
                              |  tools, combine)      |
                              +----------+----------+
                                         |
                                         v
                    +---------------------------------------------+
                    |                CONTROL PLANE                  |
                    |-----------------------------------------------|
                    |  agent registry & identity                     |
                    |  policy engine                                  |
                    |  maker-checker / human approval                 |
                    |  data controls (PII / residency)                |
                    |  security (prompt-injection / tool-abuse)       |
                    |  evals                                          |
                    |  observability                                  |
                    |  FinOps                                         |
                    |  audit                                          |
                    |  kill switch                                    |
                    +---------------------+-----------+---------------+
                                          |             |
                        model gateway --> |             | <-- tool / MCP gateway
                                          v             v
                                   +-----------+   +-------------+
                                   |    LLM     |   |    Tools     |
                                   +-----------+   +------+------+
                                                          |
                                                          v
                                              +--------------------------+
                                              |   Core banking systems     |
                                              |  (ledger, KYC, payments,   |
                                              |   sanctions lists, etc.)   |
                                              +--------------------------+
```

The control plane is not one service. It's a set of enforcement points
that every outbound call an agent makes — to a model, to a tool, into a
core banking system — has to pass through. None of them is optional and
none of them is a single point of failure for the others: an agent that
somehow evades the policy engine still can't reach a tool it was never
scoped for; an agent that reaches an unscoped tool still produces a record
in the audit trail; a compromise that defeats all of the above still
leaves the kill switch. This is defense in depth applied to the fleet, the
same principle the reference implementation applies at the single-agent
level (see [`docs/security-findings.md`](docs/security-findings.md)).

---

## 3. Control-plane capabilities

### Agent registry & identity

**What it is.** The system of record for every agent that exists: owner,
purpose, permitted tool and data scopes, which model(s) it may call, and
lifecycle state (draft / approved / production / deprecated / retired).
Identity extends this per-call: each agent runs under its own credential —
never a shared service account, never a human's — so every action is
attributable to a specific agent plus the human or workflow that invoked
it.

**Why it matters in a regulated bank.** Banks already require this
discipline for human employees (entitlement reviews, segregation of
duties) and for service accounts (IAM audits). An agent with no registry
entry and no distinct identity is an unaccountable actor with access to
money-movement or customer-data systems — the exact finding an auditor
already flags for an orphaned service account, except this one can also
initiate actions on its own. Distinct identity is also the precondition
for maker-checker meaning anything: "the approver must differ from the
requester" is unenforceable if the requester is a shared token.

### Model gateway

**What it is.** A single mediating layer between every agent and every
LLM it calls, regardless of provider: enforces which models an agent may
use, rate-limits, tags or redacts sensitive fields in prompts, and logs
every request and response centrally.

**Why it matters in a regulated bank.** "Which model, which version,
produced this output" is a question a model-risk review asks for every
material decision. A gateway makes that centrally answerable instead of
depending on every agent team having separately implemented it. It's also
the fastest lever for a bank-wide response to a newly discovered
model-level vulnerability or vendor incident — restrict or roll back at
the gateway, not inside every downstream agent's codebase.

### Tool / MCP gateway

**What it is.** The equivalent mediating layer for the tools and APIs an
agent calls to actually do something: enforces which tools an agent is
scoped to, validates call arguments against a contract before they reach
a real system, and logs every invocation and its result.

**Why it matters in a regulated bank.** This is the point where narrative
becomes action — where "the model said to do X" becomes "X happened
against a ledger or a customer record." Least-privilege tool scoping,
enforced structurally rather than documented in a prompt, is the
difference between a manipulated agent producing a wrong *answer* and a
manipulated agent taking a real, unauthorized *action*. The reference
implementation demonstrates the single-agent version of this — see
[§5](#5-reference-implementation) — but a fleet needs it enforced
centrally, not reimplemented per agent.

### Policy engine

**What it is.** A centrally defined, independently versioned set of rules
deciding what an agent may auto-decide versus what must be routed
elsewhere — by confidence, risk tier, transaction size, conflicting
signals, or any other named condition. Rules are data, not control flow
buried in an agent's code, so they can be audited, changed, and rolled
back without a deploy.

**Why it matters in a regulated bank.** Every regulated decisioning
process already has some version of this — credit policy thresholds, AML
escalation criteria, sanctions-match handling. An agent architecture needs
the same discipline applied to *when an agent is permitted to decide at
all*. A policy engine is also what turns "responsible AI principles" into
something enforceable and change-controlled, rather than an intention
living only inside a system prompt where it can silently drift.

### Maker-checker / human approval

**What it is.** An enforced workflow for anything the policy engine
routes to a human — a real queue, a reviewer identity distinct from the
requester, a captured decision with reasoning, and an SLA with escalation
for anything left unreviewed. Not a status label; a process with a
record.

**Why it matters in a regulated bank.** Maker-checker is a foundational
control in banking operations generally — payments, trade settlement —
precisely because independent second-party review is a materially
different (and stronger) control than the first party being careful. For
agents specifically, this is where the governance story either holds or
becomes theater: a review queue with no enforced follow-through is, in
effect, no review at all, and it is the single most common gap between
what an agent architecture claims and what it delivers.

### Data controls (PII / residency / minimization)

**What it is.** Enforcement of what data an agent — and the model or tool
behind it — may see, retain, or transmit: redaction or tokenization of
PII before it reaches a model, residency constraints on where prompts and
logs are processed and stored, and defined retention limits.

**Why it matters in a regulated bank.** Bank customer data sits under
regimes (GDPR, GLBA, local banking-secrecy statutes) that predate agents
and don't relax for them. An agent that pastes a full customer record into
a prompt sent to a model hosted in the wrong jurisdiction, or logs it past
a defined retention window, creates exposure independent of whether the
agent's eventual decision was correct. This has to be enforced
structurally at the gateway layer — an instruction in a system prompt is
not a data control any regulator will accept as one.

### Security (prompt-injection / tool-abuse defense)

**What it is.** Structural defenses against an agent being manipulated by
adversarial input: input validation at every trust boundary a string
crosses, decision-bearing output constrained to a fixed schema rather than
parsed from free text, and least-privilege tool access — layered so no
single control is a single point of failure for the others.

**Why it matters in a regulated bank.** Banks are an unusually attractive
target for this attack class specifically because the systems on the
other side of an agent are money-movement and customer-data systems, and
unlike a conventional software vulnerability, a successful prompt
injection doesn't require a code bug — it only requires the agent to be
convinced. The reference implementation's three findings
([`docs/security-findings.md`](docs/security-findings.md)) are a working
demonstration of exactly this attack class and the layered defense it
requires; see [§5](#5-reference-implementation).

### Evals

**What it is.** A repeatable, scored test harness — including adversarial
and red-team cases — run against the live agent pipeline, not the
underlying model in isolation, reporting metrics such as decision
accuracy, injection-resistance rate, and unsafe-action rate. In
production, these gate a release; they don't just inform one.

**Why it matters in a regulated bank.** Model risk management already
requires ongoing monitoring and validation of model performance before and
after deployment. Agents need the same discipline applied to the *system*
— coordinator logic, tool wiring, routing rules — because any of those is
just as capable of causing a decisioning failure as the underlying model.
Non-determinism means one clean run is weak evidence: a control that
passed once on a given input is not guaranteed to pass again on the same
input, which is why evals need repeated trials and a stated acceptance
threshold, not a single pass/fail per case.

### Observability

**What it is.** Structured, correlated logging and tracing across every
hop a single agent task takes — prompt, tool calls with arguments and
results, routing decision, timing, cost — tied together by one trace ID,
rather than scattered across each component's own ad hoc logs.

**Why it matters in a regulated bank.** When an agent-assisted decision is
questioned weeks later, "we'd have to reproduce it and hope it behaves the
same way" is not an acceptable answer in a regulated institution.
Observability is what lets an incident responder or examiner reconstruct
exactly what happened in one specific case — a distinct job from the audit
log's job of proving the record wasn't altered afterward.

### FinOps

**What it is.** Cost attribution and budget enforcement per agent, per
team, per use case — token and API spend tracked and capped the same way
compute and headcount already are, with alerting before a runaway loop or
a compromised agent becomes an open-ended bill.

**Why it matters in a regulated bank.** An agent that retries
indefinitely, loops on its own reasoning, or is manipulated into excessive
tool or model calls is a cost-control incident as much as a safety one.
Without per-agent budget enforcement, the first sign of a stuck or abused
agent is an invoice, not an alert. This is also the capability most often
skipped in a proof of concept and the first one that bites at fleet scale.

### Audit

**What it is.** A tamper-evident, append-only record of every decision an
agent's pipeline makes — full input, every subagent or tool's complete
structured output, the final decision and confidence, and how it was
routed — sufficient to reconstruct what happened without re-running
anything.

**Why it matters in a regulated bank.** Record-keeping sufficient to
reconstruct a system's operation is close to a literal regulatory
requirement, not a best practice — and it's the control an examiner or a
post-incident review reaches for first. The reference implementation
demonstrates the mechanism (a hash chain, verified by actually tampering
with a record and confirming detection — [§5](#5-reference-implementation))
and is explicit about what that mechanism does and doesn't prove; that
distinction — tamper-*evident* versus tamper-*proof* — matters enough to
carry forward into any production design rather than gloss over.

### Kill switch

**What it is.** A mechanism to immediately halt a specific agent, a class
of agents, or all autonomous agent activity bank-wide — cutting off
further model calls and tool access — distinct from, and faster than, a
full deployment rollback.

**Why it matters in a regulated bank.** Every other control in this list
can fail: a policy-engine bug, an eval that missed a case, a
misconfigured gateway. When one does, in a system that can move money or
affect a customer's KYC status, the ability to say *stop, now* without
waiting on a deploy pipeline is the last line of defense. Banks already
apply this logic to trading kill switches and payment circuit breakers;
agents need the equivalent, and — like those — it has to be periodically
exercised, not just built and assumed to work. An untested kill switch is
a hypothesis, not a control.

---

## 4. Mapping to EU AI Act / NIST AI RMF

This is a directional map, not a compliance claim or a legal
determination — whether any given agent is "high-risk" under the EU AI
Act's Annex III depends on deployment specifics that only counsel can
assess. It's included because a bank's AI-governance function will ask
for exactly this cross-walk, and because the two frameworks emphasize
different things worth knowing about up front.

| Control-plane capability | EU AI Act (high-risk obligations) | NIST AI RMF |
|---|---|---|
| Agent registry & identity | Art. 9 (risk management — knowing what exists to manage it) | Govern |
| Model gateway | Art. 15 (robustness/cybersecurity), Art. 12 (records) | Govern, Manage |
| Tool / MCP gateway | Art. 15 (robustness), Art. 9 (risk mitigation) | Govern, Manage |
| Policy engine | Art. 9 (risk management system) | Govern, Manage |
| Maker-checker / human approval | Art. 14 (human oversight) — the central match | Govern, Manage |
| Data controls | Art. 10 (data governance) | Govern, Map |
| Security controls | Art. 15 (robustness, resilience to exploitation) | Map, Manage |
| Evals | Art. 9 (testing), Art. 15 (accuracy), Annex IV (documentation) | Measure |
| Observability | Art. 12 (record-keeping), Art. 13 (transparency) | Manage |
| FinOps | Not a named obligation — an operational-risk control, not an AI-Act one | Govern (resource risk) |
| Audit | Art. 12 (record-keeping), Art. 13 (transparency) | Govern, Manage |
| Kill switch | Art. 14 (human oversight — the ability to intervene and halt) | Manage |

For a U.S. bank specifically, the frame examiners will actually apply to
agent-assisted decisioning is **SR 11-7 / OCC 2011-12 model risk
management** — most of this table's rows (independent testing, ongoing
monitoring, documented limitations, clear accountability) are SR 11-7
concepts before they're AI Act or NIST concepts. Treat the EU AI Act and
NIST columns as a useful cross-walk for a global institution, not a
substitute for whatever model-risk framework already governs decisioning
systems at the bank in question.

---

## 5. Reference implementation

[`shamikmondal-ai/secure-agentic-banking`](README.md) is a working,
red-teamed proof of the **execution-plane core loop and three
control-plane capabilities**, not a full fleet control plane. It's the
concrete evidence behind the claims above that are stated as demonstrated
rather than asserted:

| Capability | Proof in this repo |
|---|---|
| Security (prompt-injection / tool-abuse defense) | Three real vulnerabilities found by red-teaming the system's own coordinator, and the layered fix for each — structured decision channels, two-layer input validation, least-privilege tool scoping. Full narrative: [`docs/security-findings.md`](docs/security-findings.md). |
| Policy engine | [`agents/routing_rules.py`](agents/routing_rules.py) — six independently named, independently toggleable rules deciding `clear` / `review` / `block`, driven by confidence, conflicting signals, and risk tier rather than a single threshold. |
| Maker-checker / human approval | [`agents/review_queue.py`](agents/review_queue.py) — a real pending/resolved queue with a recorded reviewer identity and decision, closing the specific gap (a routing label with no enforced workflow behind it) named in [`docs/governance-mapping.md`](docs/governance-mapping.md), Control 6. |
| Audit | [`agents/audit_log.py`](agents/audit_log.py) — a hash-chained JSONL trail, tamper-evidence verified by actually tampering with a written record and confirming detection, not just asserting the property. |
| Evals | [`evals/`](evals) — 11 labeled cases including all three injection attacks, scoring decision accuracy, injection-resistance rate, and unsafe-action rate. Last full run: 100% / 100% / 0%, with a documented single-run non-determinism observation motivating repeated trials rather than a single pass/fail. |

Read together with §3, this repo is the proof-of-concept for the
*execution-plane discipline* a fleet control plane assumes underneath it
— least privilege, structured outputs, layered validation — plus a
working single-agent instance of the policy engine, maker-checker, audit,
and evals capabilities. It is not, and doesn't claim to be, the registry,
gateway, data-controls, FinOps, or kill-switch layers that a multi-agent
fleet needs on top.

---

## 6. Honest scope: blueprint vs. what production needs

Stated directly, in the same spirit as this repo's own limitations
section, because a reference architecture that doesn't say what it hasn't
built reads as a pitch rather than an assessment:

- **Demonstrated at single-agent scale, in code, red-teamed:** security
  controls, a policy engine, maker-checker routing, audit logging, and an
  eval harness. These are real, tested properties of one pipeline — not a
  hypothesis.
- **Blueprint only, not built here:** agent registry, model gateway, tool
  / MCP gateway as a standalone mediating layer, data controls (PII
  redaction, residency enforcement), FinOps, and kill switch. Nothing in
  this repo contradicts the design in §3 for these, but nothing in this
  repo proves it either — they're generalized from architectural
  necessity and industry practice, not from a working instance.
- **Single-agent controls don't automatically become fleet controls.** A
  policy engine that works well for one pipeline's six rules doesn't by
  itself answer how a bank reconciles policy across twenty agents with
  different risk owners — that's a governance and org-design problem
  layered on top of the technical one.
- **The audit mechanism is tamper-evident, not tamper-proof** — see
  [`docs/governance-mapping.md`](docs/governance-mapping.md)'s dedicated
  section on this distinction, and what write-once storage and external
  hash anchoring would add to close it. That gap doesn't shrink at fleet
  scale; it multiplies by the number of agents whose audit trails now
  depend on the same unclosed gap.
- **No confirmed regulatory classification, for this system or the
  pattern in general.** §4's mapping is directional, not a legal opinion,
  and shouldn't be cited as one.
- **Cost, latency, and organizational ownership of the control plane
  itself are out of scope here.** Who operates the registry, who owns the
  policy engine's change-control process, and what this adds to
  agent-call latency are real production questions this document doesn't
  attempt to answer.
