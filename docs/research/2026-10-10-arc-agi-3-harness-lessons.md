# Harness lessons from ARC-AGI-3: AVO, VISTA, and the Provider Adapter

*Literature survey, 2026-10-10. Not a measured run: figures are as reported by their
sources, and most were read as search extracts or secondary coverage (see the next
section). Findings are left as written; the [roadmap](../roadmap.md) tracks the
accepted recommendations and [rejected ideas](../rejected-ideas.md) records the
declined ones. ReSchema's share is in its own
[roadmap](https://github.com/Lewdwig-V/reschema/blob/main/docs/roadmap.md).*

All three ideas are useful to Warranted and ReSchema. None of them belongs in ReSchema's measured agent loop, and the most valuable lessons concern *how a harness is built and reported* rather than any new search algorithm. NVIDIA AVO is an evolutionary-search method in which an autonomous coding agent replaces the fixed LLM mutation step, and a candidate is committed only when it passes correctness and matches or beats the best score so far. MIT VISTA is a model-agnostic "visual harness": it gives a frontier model lossless memory of past frames, exact-read tools and split notes files, which took Claude Opus 5.0 from 40.68 to 100 RHAE on the public ARC-AGI-3 games. The "OpenAI Provider Adapter" is ARC Prize's `openai.responses.v1` adapter run under the `continuous_conversation` state strategy. It replays the provider's native reasoning state verbatim, and with it the same GPT-6 Astra weights reportedly scored 99.9% against 62.7% under the Standard harness. The three are tied together by ARC-AGI-3, where each reports that the harness, not the model, moved the score. Warranted already owns the hard half of each idea: sound checkers, host-drawn entropy, a ledger of every attempt, budgets and pinned model identity. The recommendations below therefore aim almost entirely at Warranted: the N3 strategy layer, the M7 model-adapter limits, M10 evaluation integrity and M6 replay. ReSchema benefits mainly through offline judge-hardening campaigns that run on Warranted once the M8 port is complete.

## Three identifications rest on partial source access

Before relying on any number here, note the limits on the evidence. The researchers' egress proxy blocked arxiv.org, arcprize.org, openai.com, news.mit.edu, the VISTA project page and most aggregators. Most paper-level facts therefore come from search-engine snippets and secondary coverage, not from the primary PDFs or blog posts. The exceptions were the VISTA GitHub README, read in full, and shallow clones of `arcprize/arc-agi-3-benchmarking` and `openai/openai-agents-python`, read locally. Exact figures should be checked against the primaries before anyone cites them onward.

Two identifications are inferences. **"MIT VISTA (optimising action pathways)"** was matched to *VISTA: A Visual Harness for Reasoning in an Interactive World* (arXiv:2610.02200, 1 Oct 2026, Han, Hu, Qiu, Wu and Kaiming He) ([arXiv](https://arxiv.org/abs/2610.02200); [GitHub](https://github.com/joshhhhhan/VISTA)). It is the only 2026 VISTA confirmed as MIT, and its headline metric rewards reaching goals in fewer actions. Its path efficiency emerges from perception and memory, though; no planner scores action paths. Two near-misses fit other readings of the phrase. A non-MIT VISTA does hypothesis-driven prompt optimisation ([arXiv:2603.18388](https://arxiv.org/abs/2603.18388)). MIT CSAIL's EnCompass searches over agent execution paths but is not called VISTA ([arXiv:2512.03571](https://arxiv.org/abs/2512.03571)). **"OpenAI Provider Adapter"** was matched to the ARC Prize harness: "Provider Adapter" as a capitalised proper noun appears officially only there ([README](https://github.com/arcprize/arc-agi-3-benchmarking/blob/main/README.md)). The fallback reading is the OpenAI Agents SDK `ModelProvider` layer ([docs](https://github.com/openai/openai-agents-python/blob/main/docs/models/index.md)), and the lessons below still apply under either reading. "AVO" itself has two meanings: the March 2026 kernel-evolution paper ([arXiv:2603.24517](https://arxiv.org/abs/2603.24517)) and the August 2026 general agent harness built from it ([NVIDIA blog](https://developer.nvidia.com/blog/nvidia-avo-reaches-100-on-arc-agi-3-demonstrating-a-frontier-level-general-purpose-architecture-for-long-horizon-autonomous-agents/)). No official AVO code was found.

## One benchmark, three harnesses, and the same lesson

**AVO** targets the FunSearch/AlphaEvolve lineage. Those systems confine the LLM to a one-shot mutation role; AVO replaces that step with an agent that "consults the current lineage, a domain-specific knowledge base, and execution feedback to propose, repair, critique, and verify" edits ([arXiv HTML](https://arxiv.org/html/2603.24517v1)). A kernel version is committed only when it "passes correctness and matches or improves the best committed benchmark score". Failed attempts shape the trajectory but never enter the lineage ([EvoMap](https://evomap.ai/blog/nvidia-avo-agentic-variation-operators)). A self-supervision mechanism detects stalls and edit cycles, reviews the whole trajectory, and proposes new directions ([alphaXiv](https://www.alphaxiv.org/abs/2603.24517)). After **7 days on B200**, the self-reported kernels beat cuDNN by up to **3.5%** and FlashAttention-4 by up to **10.5%**, and transfer to GQA took about **30 minutes** ([arXiv HTML](https://arxiv.org/html/2603.24517v1)). Read as a design, this is (1+1)-style hill climbing over one committed lineage, with a hard correctness gate in front of the score. Diversity comes from the supervisor's proposals, not from a population.

NVIDIA later "kept the core loop, replaced the task interface". The resulting AVO harness had persistent memory, a stagnation supervisor and an inspect/plan/implement/evaluate loop. Running Claude Opus 5 on an exact 64×64 text grid, it reached **100 RHAE on all 25 public ARC-AGI-3 games** using **6,624 actions**, against 7,542 for VISTA. NVIDIA says the VISTA comparison is not a controlled ablation ([NVIDIA blog](https://developer.nvidia.com/blog/nvidia-avo-reaches-100-on-arc-agi-3-demonstrating-a-frontier-level-general-purpose-architecture-for-long-horizon-autonomous-agents/); [The New Stack](https://thenewstack.io/nvidia-avo-arcagi3-benchmark/)).

**VISTA** argues that models fail long-horizon interactive tasks because the harness gives them a lossy, encode-once view of the world. Its tools are `play`, `inspect` (revisit old frames and regions), `read_pixels` (exact values) and `history`. It keeps two notes files: `GUIDE.md`, which persists across levels, and `WORKING.md`, which is cleared when a level advances. It runs on both Claude Code and Codex CLI backends ([GitHub README](https://github.com/joshhhhhan/VISTA)). A short prompt asks the model to state an expected outcome before each action and to record what changed afterwards ([arXiv HTML](https://arxiv.org/html/2610.02200v1)). Opus 5.0 went from **40.68 to 100 RHAE**, and GPT-5.6 Sol reached **99**, against **13.33** on the organisers' text-grid interface ([arXiv PDF](https://arxiv.org/pdf/2610.02200v1)). Secondary write-ups report an ablation ladder of 13.33 → 47.32 (images) → 70.05 (+notes) → 99 (+lossless look-back). Only 13.33 was confirmed against a primary extract ([hyper.ai](https://hyper.ai/en/stories/e12ca0b9ba13087097d720650a4c744e)).

**The ARC Provider Adapter** splits four concerns. The agent scaffold owns action parsing. The provider adapter "translates a normalized model request to one SDK API call and normalizes the response". A state strategy builds the next request from the last *accepted* state. Config selects all three ([runtime-state.md](https://github.com/arcprize/arc-agi-3-benchmarking/blob/main/docs/runtime-state.md)). The OpenAI adapter runs with `store:false`, requests `reasoning.encrypted_content`, and replays "every native `response.output` item" into the next turn. It drops only two SDK fields that the input schema rejects. A boundary validator raises before any call if the request violates the strategy's invariants ([openai_runtime.py](https://github.com/arcprize/arc-agi-3-benchmarking/blob/main/benchmarking/openai_runtime.py)). "The harness accepts a provisional state only after it parses a valid action", so a retry cannot carry orphaned reasoning forward ([runtime-state.md](https://github.com/arcprize/arc-agi-3-benchmarking/blob/main/docs/runtime-state.md)). The precursor was OpenAI's July post. Keeping reasoning and compaction moved GPT-5.6 Sol from **13.3% to 38.3%** on the public set with about 6× fewer output tokens, because the official harness had discarded reasoning after every action ([OpenAI](https://openai.com/index/how-two-settings-tripled-our-arc-agi-3-scores/)). Then came GPT-6 Astra: **62.7% Standard vs 99.9% Provider Adapter**, with identical weights ([ARC Prize](https://arcprize.org/blog/astra)). ARC Prize requires the two conditions to be "reported separately and clearly labeled" ([README](https://github.com/arcprize/arc-agi-3-benchmarking/blob/main/README.md)). The gap is not universal: GPT-6 Luna scored 0.19% vs 0.59% ([ARC Prize on X](https://x.com/arcprize/status/2102485016726913297)).

All three systems report **harness-driven jumps of 2× to 7× on the same weights**, and all three reported them on the public ARC-AGI-3 set only. That is directly relevant to Warranted. Its general-purpose-harness proposal already names ARC-AGI-3 as the use case that forces N4 (stateful sessions) and N2 (score as objective), and it warns that "the public sets are widely published, so capability claims need private or held-out tasks" ([W general-purpose-harness.md](../proposals/general-purpose-harness.md)).

## Warranted already owns the evaluator half, and its refusals fence the rest

The repos (ReSchema `27801ec`, Warranted `0772d2f`, v0.3.0) have no evolutionary or population search. Both run a single-trajectory loop: propose, strict check, structured feedback, repair. That loop is unusually well hardened. Warranted's boundaries table fixes the checker as "Deterministic and Lean, no model-judgment gates" and the exploration policy as "One fixed serial policy" ([W design.md](../design.md)). Invariants in its AGENTS.md add more constraints. Optimisers "cannot weaken the target, checker, applicability test, or budget enforcement". Side effects are never blindly retried. Replay cannot call live tools. Failed attempts and optimisation cost must be reported ([W AGENTS.md](../../AGENTS.md)). Since ReSchema is being rebuilt on Warranted under M8, which replaces `engine.TaskStore`, the gates, the flail guard, `memory.py`, the MCP server and the `tools/dogfood/` opencode driver, any new LLM-calling or strategy work belongs in Warranted ([R m8-rebuild-on-warranted.md](https://github.com/Lewdwig-V/reschema/blob/27801ec2933fa304cd03ca149d2fafb17ed2cf48/docs/proposals/m8-rebuild-on-warranted.md)). M8 also freezes gate changes (#120, #122), #113/#114, the agent runner (#128/#129) and #88 for the duration of the port.

The documented refusals rule several readings of these papers out entirely:

| Refusal | Where | What it rules out |
| --- | --- | --- |
| §2: LLM-generated tests or learned verifiers as oracle | ReSchema | Learned verifiers may rank candidates but never accept |
| §4: Showing judge-strength signals to the agent | ReSchema | No coverage, kill rates or hidden progress visible to the agent |
| §6: pass@k, union-of-attempts, pinned hidden seeds | ReSchema | "Union-of-agents roughly doubles single-agent rates… the opposite of what E measures" |
| Neutral-prompt rule | ReSchema `tools/dogfood/prompt.py` | Strategy coaching counts as "solver scaffolding" |
| A custom agent loop, reasoning DSL, or mandatory model-facing tools | Warranted | Reopen only on a measured head-to-head |
| Constrained decoding of worker reasoning | Warranted | Reasoning stays free; typing happens at the boundary |
| An LLM critic in place of an available sound checker | Warranted | A model does not replace or outvote a symbolic or executable check |
| Optimising against a model monitor | Warranted | A monitor "is never an objective for an optimiser or a strategy layer" |

Sources: [R rejected-ideas.md](https://github.com/Lewdwig-V/reschema/blob/27801ec2933fa304cd03ca149d2fafb17ed2cf48/docs/rejected-ideas.md), [R tools/dogfood/prompt.py](https://github.com/Lewdwig-V/reschema/blob/27801ec2933fa304cd03ca149d2fafb17ed2cf48/tools/dogfood/prompt.py) and [W rejected-ideas.md](../rejected-ideas.md).

Taken together, these mean search must run *outside* the measured agent boundary or be fully priced into it. Fitness must come from sound checkers. Any change to the prompt or interface is a new measurement configuration and starts a new metric epoch.

## Prioritised recommendations, highest leverage first

| # | Recommendation | Source idea | Target | Effort and timing |
| --- | --- | --- | --- | --- |
| 1 | Keep raw provider bytes beside litellm's normalised response; validate request invariants at config time | Provider Adapter | Warranted M7: `src/warranted/_litellm.py`, `docs/reference/model-adapters.md` "Limits" | Small; now |
| 2 | Make reasoning-state retention an explicit, pinned run condition, and report harness conditions separately | Provider Adapter + OpenAI July post | Warranted `RunConfig`/`service_id`; M10 budget-indexed reports; ReSchema M8 metric epoch | Medium; before M8 live runs |
| 3 | Build N3 as an AVO-shaped strategy: host-gated commit, lineage parent selection, deterministic stagnation trigger | AVO | Warranted N3 in `_tasks.py` (`Project._episode`), ARC-AGI-1/2 fixture, then Minkowski N2/N8 | Large; next after M7 |
| 4 | Run AVO-style operators offline as judge-hardening campaigns | AVO + CAKE | ReSchema roadmap P0/P1 (kill rate, hackability audit, #109 input pool, #122 kill corpus) as Warranted campaigns; M10 adequacy probes | Medium; after M8 S-steps land |
| 5 | Use VISTA as the template for ablation and trajectory design, not for perception | VISTA | Warranted M5 A–E, M6 Dream-RSI, N11; ReSchema #128/#129 after the freeze | Medium; data-gated |

### 1. Close the adapter's evidence gap first

Warranted's model-adapter reference states the limitation plainly: "litellm's normalized response is the recorded evidence. Normalization can drop provider-specific fields, and the raw provider bytes are not kept" ([W model-adapters.md](../reference/model-adapters.md)). The ARC adapter's central design choice is the reverse. It treats native output as an opaque log, replays it byte-faithfully, mutates only an allowlist of known-rejected fields, and logs sanitised descriptors without opaque bodies ([openai_runtime.py](https://github.com/arcprize/arc-agi-3-benchmarking/blob/main/benchmarking/openai_runtime.py)). For a project named Warranted, keeping the raw response as a SHA-256 artifact in the ledger before parsing is the cheapest improvement available. It also fits the existing rule that "raw stdout, stderr, provider responses, and known usage are recorded before observations are formatted".

The ARC boundary validator (`validate_continuous_conversation_request`) generalises the existing `PARAMETERS` allowlist and the native-structured-output probe in `_litellm.py`. Incompatible combinations should fail at config time, before any reserved attempt is spent. The ARC adapter's `SummaryCompactionRuntimeAdapter` is rejected for Anthropic in exactly this way ([runtime-state.md](https://github.com/arcprize/arc-agi-3-benchmarking/blob/main/docs/runtime-state.md)). The OpenAI Agents SDK reached the same conclusion with `strict_feature_validation`, which turns silent field-dropping into a hard error ([docs](https://github.com/openai/openai-agents-python/blob/main/docs/models/index.md)).

This recommendation does **not** reopen per-API connectors. M7 decided that "Warranted maintains no per-API connectors" and adds "no second call shape" ([W m7-model-providers.md](../proposals/m7-model-providers.md)). Raw-byte capture and config-time validation both fit inside the litellm adapter.

### 2. Treat reasoning retention as a measured condition, not a default

Warranted's worker protocol sends only system, user and assistant text. Thinking is "recorded but never become[s] the command" and is not replayed. That makes it the "Standard harness" in ARC's terms. OpenAI's own data shows discarded reasoning cost GPT-5.6 Sol roughly two-thirds of its score ([OpenAI](https://openai.com/index/how-two-settings-tripled-our-arc-agi-3-scores/)). The Astra result shows the same pattern at the frontier ([ARC Prize](https://arcprize.org/blog/astra)).

The recommendation is not to switch it on. It is to make "reasoning state replayed / not replayed" a pinned field in the run's model record and `service_id`, so it is covered by the existing refusal to resume with any configuration change. Campaigns should then report the two conditions separately, as ARC requires. M10's planned budget-indexed reports are the natural home for that labelling.

This matters most for M8. The proposal already says "2C results are not comparable across this change: the port starts a new metric epoch". The epoch's identity should include the reasoning-retention setting and the adapter. Otherwise a later adapter upgrade could silently move E. A Responses-native or encrypted-reasoning path may require more than litellm normalises, which would conflict with the "no second call shape" decision. Treat it as a reopen that needs a measured A/B on the same tasks and budgets, under the same rule Warranted applies to its own refusals.

### 3. Build the N3 strategy layer in AVO's shape, with the host holding the gate

N3 is already specified as "a *strategy* that decides how a run spends its budget… several independent candidates, or candidates refined from the best so far… The strategy chooses which candidates to try; the host keeps the checks, acceptance, and budget enforcement". The proposal's order puts N3 first, on ARC-AGI-1/2, followed by N1/N2 on a Minkowski round and N8 chains ([W general-purpose-harness.md](../proposals/general-purpose-harness.md)). AVO supplies a tested shape for it.

Parent selection becomes "seed the episode from the best accepted lineage member's `workspace.json` and feedback". Today `Project._episode` always continues from `index-1`. The commit gate is AVO's rule: correctness first, then N2's deterministic objective with its minimum-improvement and regression-tolerance fields, applied by the host. Promotion of the best accepted result is N8. The Minkowski round already describes this loop on paper: gate on tests, Miri and Loom; measure iai-callgrind instruction counts; promote. Every child is a charged submission, so invariant 7's cost accounting comes for free.

Three adjustments keep it inside the rules:

- **The stagnation supervisor triggers deterministically on the host**, from no-improvement counts and `DuplicateGuard` near-repeat hits. An LLM may *propose* new directions as part of the worker, but it never accepts and is never the objective. This respects the refusals of LLM critics and monitor-optimisation.
- **Failed children are logged with their parent link.** This fixes the audit gap critics found in AVO, where failures are invisible in the lineage ([EvoMap](https://evomap.ai/blog/nvidia-avo-agentic-variation-operators)). Warranted's ledger already does it.
- **Elites are re-checked on fresh host draws before promotion.** Fresh per-submission entropy makes fitness stochastic. A ReSchema-derived checker that selects on lucky hidden draws would overfit.

`DuplicateGuard`'s window and edit floor will need scoping per lineage, or they will refuse legitimate small mutations of a rejected parent. Any multi-candidate result must be labelled as its own condition and never compared with single-agent E, because ReSchema §6 refuses union-of-attempts headlines. AVO's own ablations could not be retrieved, so which component carries the gain is unknown. Build the supervisor as a switchable part and measure it.

### 4. Point AVO at the judge, offline, where ReSchema already wants it

ReSchema's backlog already describes several AVO-shaped circuits, as long as the operator attacks the *judge* rather than solving the task:

- **Mutation kill rate:** mutate each seed's reference C, run the mutants through the unchanged gate, and report the kill rate "in CI/benchmark artifacts only".
- **Adversary-model hackability audit:** give an adversary a fixed budget to produce a wrong-but-accepted model.
- **Offline hidden-input pool** from coverage-guided fuzzing at `corpus_build`.
- **#122 judge-private kill corpus**, aimed at the #109 magic-value blind spot (`if (x == 0x7E3A99B1)` is invisible to uniform draws).

Sources: [R roadmap.md](https://github.com/Lewdwig-V/reschema/blob/27801ec2933fa304cd03ca149d2fafb17ed2cf48/docs/roadmap.md), [#109](https://github.com/Lewdwig-V/reschema/issues/109) and [#122](https://github.com/Lewdwig-V/reschema/issues/122).

In each circuit the LLM agent is the variation operator and the existing gate is the fitness function. This is AVO with the evaluator unchanged. Rejected-ideas §5 explicitly allows concolic and coverage tools as offline input generators. NVIDIA/CMU's follow-up, CAKE, adds the matching step on the harness side: repeated failures become verifier rules, gated by corpus tests ([CAKE](https://arxiv.org/pdf/2608.12629)). In ReSchema that is the 3A "negative-regression garden". In Warranted it is M10's contract-adequacy probes, where known-bad candidates are re-run on every revision.

Because of the M8 freeze, build these as Warranted campaigns over the ported `ProgramReplay` and `FunctionFuzz` checkers rather than as edits to `engine.py`. Keep their outputs off every agent-visible surface (§4). Kill rates and hackability numbers may become a published per-epoch corpus property, never a live per-submission signal.

### 5. Take VISTA's discipline, not its eyes

VISTA's perceptual machinery (rendered PNGs, zoom, pixel reads) has no counterpart in binary reverse engineering or text-domain checking. AVO's choice of an exact text grid on the same benchmark also suggests the shared lesson is *lossless, exact access to the source of truth*, not vision. Warranted already provides that. The worker has a shell over captured bytes, and `feedback-NNN.json` and `workspace.json` are kept verbatim.

Three VISTA ideas carry over:

- **Ablation reporting.** VISTA reports its gain by component: interface, then notes, then lossless look-back. Warranted's M5 A–E comparison and N11's "shown-versus-outcome" usefulness reports should break down their results the same way.
- **The `GUIDE.md`/`WORKING.md` split with scoped invalidation.** This maps onto `MemorySpec` scopes and N11 consolidation. Promote a rule to persistent memory only after it is verified across cases, which Warranted's claims machinery can enforce.
- **Predict-then-verify.** This is a prompt intervention. In ReSchema it would breach the neutral-prompt rule in any baseline run. In Warranted it would only be admissible as a separately measured condition, never as a mandatory tool.

ReSchema's E (cost-shaped by probes and submissions) is already an RHAE-like path-efficiency metric. Warranted has nothing equivalent outside planned budget-indexed reports.

Trajectory optimisation proper belongs in M6, Dream-RSI: "evolve only the scheduling policy using replayed discovery trees, with the model, checkers, contracts, and budgets frozen" ([W roadmap.md](../roadmap.md)). That work is data-gated. ReSchema's live trajectories come from three `gemma4:26b` campaigns, and no Warranted live run has finished the revision sequence. The #128 continuation controller, which must "not reset E costs", is the concrete ReSchema pathway target, but it is frozen until the port lands.

## Where the ideas do not fit

Several readings of these papers should be declined outright:

- **No AVO-style search inside ReSchema's agent-facing loop.** It would turn E into a pass@k number (§6) and could expose fitness signals (§4).
- **No LLM critic or learned verifier as acceptance or as an optimisation objective**, whatever AVO's "critique" step suggests (ReSchema §2; Warranted's critic and monitor refusals).
- **No mandatory VISTA-style tool suite or structured action interface for Warranted's worker.** That is a refused custom loop; reopen only on a measured head-to-head.
- **No tool-call translation layer.** Warranted's protocol is a single strict `{"command": string}`, and tool calls count as infrastructure failures. ARC's DeepSeek trick of forcing a `submit_action` tool as a structured-output carrier is also excluded, because Warranted avoids forced tool use deliberately ("Current Claude models reject forced tool use with a 400").
- **No retrying, streaming or fallback-routing "OpenAI-compatible proxy".** It would break invariant 5 and the pinned `service_id`.
- **No adoption of 100%-on-public-set results as evidence of capability.** AVO, VISTA and Astra all report on published games, and Warranted's own ARC section already discounts public sets.

## Conclusion

The papers' shared finding is that harness choices can be worth more than a model generation. For a project whose purpose is warranting results, that is mainly a measurement-integrity problem: the adapter, the reasoning-retention setting and the strategy layer are part of what a result means, so they must be pinned, recorded raw and reported as separate conditions. Warranted's existing invariants make that cheap to do now and expensive to retrofit after M8 live runs begin.

The algorithmic ideas, AVO's agentic variation and VISTA's trajectory discipline, arrive later and in specific places. They belong in the N3 strategy layer with host-held gates, in offline judge-hardening where the agent attacks the checker rather than the task, and in M6 once there are enough trajectories to learn from. If a different "VISTA" or "provider adapter" was meant, recommendations 1, 2 and 5 still hold, because EnCompass's branchpoint search and the Agents SDK's strict feature validation point the same way.
