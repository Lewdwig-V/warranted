# Rejected ideas

Ideas that were considered and declined, with the reason, so they are not
proposed again without new evidence. Each entry names its source and, where one
is foreseeable, what would justify reopening it. An entry is reopened by a
reviewed change to this file, not by citing the same evidence again. Accepted
work lives in the [roadmap](roadmap.md).

Entries marked **Deferred** are not rejected on principle; they wait for the
stated condition.

## From the neurosymbolic harness survey (2026-10-09)

Source: [neurosymbolic agent harnesses](research/2026-10-09-neurosymbolic-agent-harnesses.md).
The accepted extensions are [M10](roadmap.md#m10--evaluation-integrity-and-verifier-hardening).

### A custom agent loop, reasoning DSL, or mandatory model-facing tools

Rejected. The worker keeps shell and file capabilities, with typed structure only
at the trust boundary (`Verdict`, receipts, the submission convention).
[CodeAct](https://proceedings.mlr.press/v235/wang24h.html) found executable
Python actions beat JSON tool calls by up to 20 percentage points, and
[Tam et al.](https://arxiv.org/pdf/2408.02442) found strict format constraints on
reasoning lower accuracy. AGENTS.md already forbids internal ledger records
becoming a mandatory sequence of model-facing tools.

Reopen if: a measured run shows a fixed worker loop losing to a structured one
on the same tasks, budgets, and model.

### Constrained decoding of worker reasoning

Rejected, for the same [Tam et al.](https://arxiv.org/pdf/2408.02442) result.
Reasoning stays free; conversion to a typed form happens at the boundary, where
the host validates it.

Reopen if: the same as above.

### A Datalog, ASP, or probabilistic-logic engine in the core

Rejected. A solver or logic engine is domain machinery and enters through a
checker or job image, as the
[N7 trusted symbolic engine](proposals/general-purpose-harness.md#n7-no-place-for-a-trusted-symbolic-engine)
proposal already plans for SMT. In "LLM translates, solver infers" systems the
failures sit in translation, not inference
([arXiv:2506.18383](https://arxiv.org/pdf/2506.18383)), so a core engine would not
address the dominant error.

Reopen if: two use cases need the same engine with the same trust treatment and a
checker image cannot provide it.

### Differentiable logic programming (Scallop, Lobster)

Rejected for the harness.
[Lobster](https://arxiv.org/abs/2503.21937v2) and its lineage are training-time
techniques. Warranted does not train models.

Reopen if: a training goal enters the roadmap.

### A general policy language (Cedar, Rego) for gates

Rejected for gates. A gate is a closed set of required checks with no opt-out,
enforced by tested host code; a policy language would add a framework with no
second use case and a new place for opt-outs to hide. Rule monitors also
guarantee only the rules as written ([survey](research/2026-10-09-neurosymbolic-agent-harnesses.md#runtime-enforcement-and-provenance-research-mostly-confirms-warranteds-boundaries)).

**Deferred** for tool-call monitoring: default-deny monitors such as Cedar in
[Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.md)
belong in the reviewed design for host-mediated external-effect operations, not
before it.

### An LLM critic in place of an available sound checker

Rejected. In a GPT-4 generator and verifier loop on Blocksworld, the LLM verifier
accepted 38 invalid plans out of 100 instances, and self-critique lowered
performance against the VAL validator
([arXiv:2310.08118](https://arxiv.org/pdf/2310.08118)). Where a symbolic or
executable check exists, a model does not replace or outvote it.

This does not decide [M3a](roadmap.md#m3a--jev-as-a-system-1-classifier-and-judge):
contract-designated judgment gates for obligations with no symbolic check remain
deferred, and need a measured false-acceptance rate first
([evaluation design](experiments/evaluation-design.md)).

### Optimising against a model monitor

Rejected. Optimising against a chain-of-thought monitor produced obfuscated reward
hacking ([arXiv:2503.11926](https://arxiv.org/abs/2503.11926v1)). A model monitor
may report; it is never an objective for an optimiser or a strategy layer.

Reopen if: never on current evidence.

### LLM-reasoning guards as enforcement

Rejected as enforcement. Guards that reason with a model, such as ShieldAgent
and GuardAgent, are not host-enforced isolation, and AGENTS.md forbids claiming
enforcement from a prompt. They may be reported as advisory signals.

### Temporal knowledge-graph memory in place of claim dependencies

Rejected. Validity intervals, as in
[Zep/Graphiti](https://arxiv.org/html/2501.13956v1), record when a fact was
believed, not what depends on it. Stale-dependency rejection needs the
host-declared parent edges that claims already have
([claims and acceptance](reference/claims-and-acceptance.md)).

### Durable-execution frameworks for side-effect recovery

Rejected. Temporal-style recovery re-executes incomplete activities, and its
history is not written by the host
([Temporal](https://docs.temporal.io/develop/go/integrations/google-adk)).
Invariant 5 requires an unknown external outcome to be reconciled or left
blocked, never retried.

Reopen if: never for side effects; it may still be considered for scheduling
that has no external effects.

### Model-improvised compensation for failed side effects

Rejected. LLMs are unreliable at working out how to reverse a failed operation
([arXiv:2605.03409](https://arxiv.org/pdf/2605.03409)), and irreversible effects
cannot be undone once they leave the gate
([Atomix](https://arxiv.org/html/2602.14849v1)). Reconciliation and compensation
are declared by the domain.

### Blacklist filtering of proof text

Rejected. Agents injected `local notation` past a filter that blacklisted only
`macro`, `axiom`, `#exit`, and `sorry`
([arXiv:2609.04170](https://arxiv.org/pdf/2609.04170)). Proofs are judged by
kernel re-checking against a trusted statement and an axiom whitelist, as the
[proof verifier](reference/proof-verification.md) already does.

### Majority vote over formal translations

Rejected as a resolution. LINC-style multiple translations are useful evidence of
ambiguity, but disagreement is recorded as a gap on the target, not settled by
vote (see M10's owner-approved formalisation).

### Signed in-toto receipts

**Deferred.** Signatures matter once a second, untrusted party consumes
Warranted's exports. No ratified in-toto predicate for agent tool calls exists
yet. Revisit when such a consumer exists.

### Determinism re-runs and fresh-interpreter checks in the core

**Deferred** to checker patterns. The
[AlphaEvolve hardening guide](https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/developer-guide/reward-hacking-prevention)
techniques are useful, but domains apply them inside their own checker jobs.
Revisit if two domains need the same mechanism.

## From the ARC-AGI-3 harness survey (2026-10-10)

Source: [ARC-AGI-3 harness lessons](research/2026-10-10-arc-agi-3-harness-lessons.md).
The accepted recommendations are in [M9](roadmap.md#m9--general-purpose-neurosymbolic-harness-five-use-cases),
[M10](roadmap.md#m10--evaluation-integrity-and-verifier-hardening), and
[M6](roadmap.md#m6--dream-rsi-adaptation-for-search-improvement). A VISTA-style
mandatory tool suite for the worker is already declined above, under a custom
agent loop or mandatory model-facing tools.

### Tool-call translation, or a forced tool call as the structured-output carrier

Rejected. The worker protocol is one strict `{"command": string}` reply, and a
tool call in a reply is an infrastructure failure
([model adapters](reference/model-adapters.md)). ARC Prize's DeepSeek adapter
forces a `submit_action` tool to obtain structured output, but current Claude
models reject forced tool use, and translating tool calls between providers
would add a second call shape that M7 declined.

Reopen if: a model needed for a measured campaign offers no other way to return
structured output, and the change keeps a single call shape.

### A retrying or fallback-routing provider proxy

Rejected. Retrying a model call, or routing it to another provider on failure,
breaks invariant 5 (no blind retry) and the pinned model identity a run resumes
under. Each attempt stays one call to one pinned model.

Reopen if: never for the pinned identity; a provider switch is a new run.

### Public-set scores as capability evidence

Rejected as evidence. AVO, VISTA, and GPT-6 Astra each report about 100% on the
published ARC-AGI-3 games, with gains of two to seven times from the harness
alone. Warranted's ARC-AGI-3 use case already requires private or held-out tasks
for capability claims; these results are evidence about harness design only.
