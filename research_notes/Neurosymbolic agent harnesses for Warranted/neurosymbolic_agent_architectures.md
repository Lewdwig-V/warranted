# Neurosymbolic Agent Architectures and Frameworks (state as of October 2026)

Scope: general-purpose harnesses that combine LLM agents with symbolic components for planning, reasoning, knowledge, and program execution. Pure math theorem proving is excluded. Research budget: about 18 searches and fetches. One primary-doc fetch (AWS Bedrock user guide) failed with a DNS error, so the AWS details below come from AWS announcement and blog pages only. Search-tool summaries were used as intermediaries. Figures marked "secondary" were not checked against the primary paper.

Cross-cutting taxonomy used below: (a) **LLM as generator, symbolic system as verifier/critic** (LLM-Modulo, Bedrock AR checks); (b) **LLM as translator/formalizer, symbolic solver does the inference** (LLM+P, Logic-LM, LINC, SatLM, CodeLogician); (c) **program as the action/plan representation** (CodeAct, PoT, ViperGPT, DSPy); (d) **symbolic/structured memory** (temporal KGs, TMS-style dependency tracking); (e) **cognitive-architecture framing** (CoALA). This follows the three-way split in the IJCAI 2025 survey (Symbolic→LLM, LLM→Symbolic, LLM+Symbolic) — [Yang et al., IJCAI 2025 survey track, arXiv:2508.13678](https://arxiv.org/abs/2508.13678v1); [IJCAI proceedings](https://www.ijcai.org/proceedings/2025/1195).

---

## 1. LLM + classical planners (LLM+P, PDDL generation, LLM-Modulo, generate-test-critique)

### Takeaway
The best-supported pattern is a generate-test loop. An LLM proposes a candidate and *sound external verifiers* accept or reject it, with feedback on rejection. Here correctness comes from the verifier, not the LLM. LLM self-critique is measurably unreliable. Translating natural language into PDDL works on clean descriptions but degrades on natural text and is very sensitive to small errors in action models.

### Cited Findings
- **LLM+P (Liu et al., Apr 2023, arXiv:2304.11477):** the LLM converts a natural-language problem into a PDDL problem file, a classical planner solves it, and the plan is translated back into language. The authors report that LLM+P finds optimal solutions for most problems, while LLMs alone fail to produce even feasible plans for most problems. A domain PDDL file is assumed to be given. — [papers.cool summary of 2304.11477](https://papers.cool/arxiv/2304.11477); [DeepAI listing](https://deepai.org/publication/llm-p-empowering-large-language-models-with-optimal-planning-proficiency)
- A later summary notes that LLM+P does not itself recognize when a prompt needs the planner, so routing remains external. — [paperswithcode listing](https://cs.paperswithcode.com/paper/llm-p-empowering-large-language-models-with) (secondary)
- **LLM-Modulo (Kambhampati et al., ICML 2024 position paper):** argues that autoregressive LLMs cannot plan or self-verify by themselves. It recasts LLMs as "approximate knowledge sources" in a bidirectional loop with external model-based verifiers/critics. LLMs may also help *build* those verifiers' models. The paper contrasts this with simply pipelining the problem to a solver. — [PMLR v235](https://proceedings.mlr.press/v235/kambhampati24a.html); [arXiv:2402.01817](https://arxiv.org/abs/2402.01817)
- **Empirical LLM-Modulo test (Gundawar, Valmeekam, Verma, Kambhampati, Nov 2024):** an LLM paired with a complete set of sound verifiers, with re-prompting on failure. Reports significant gains on four scheduling domains (TravelPlanner trip planning; NaturalPlan trip, meeting and calendar planning). It claims every emitted output is guaranteed correct, but that holds only if the verifiers are sound and complete. — [arXiv:2411.14484](https://arxiv.org/html/2411.14484v1)
- **Self-critique fails (Valmeekam, Marquez, Kambhampati, 2023, arXiv:2310.08118):** in a GPT-4 generator/GPT-4 verifier loop on Blocksworld, self-critique *reduced* plan-generation performance compared with a sound external verifier (VAL). The LLM verifier was correct on 61/100 instances, with 54 true positives and **38 false positives** (invalid plans accepted). Binary and detailed feedback had minimal difference in effect. — [arXiv:2310.08118](https://arxiv.org/pdf/2310.08118); [NeurIPS 2023 workshop](https://neurips.cc/virtual/2023/82802)
- **Stechly, Valmeekam, Kambhampati, "On the Self-Verification Limitations of LLMs on Reasoning and Planning Tasks" (ICLR 2024 per summary):** across formal tasks, sound external verifiers significantly improve performance even without elaborate critique. LLM verifiers hallucinate satisfied constraints, for example non-existent edges in graph coloring. — [review summary](https://themoonlight.io/review/on-the-self-verification-limitations-of-large-language-models-on-reasoning-and-planning-tasks) (secondary)
- **Formalizer limits (Huang & Zhang, ACL 2025, "On the Limit of Language Models as Planning Formalizers"):** large enough models can produce *complete* PDDL domain and problem files from descriptions and beat direct plan generation, and they are robust to lexical perturbation. Performance falls as descriptions become more natural-sounding (less templated), and the paper gives a detailed error analysis. — [arXiv:2412.09879](https://arxiv.org/html/2412.09879v4)
- **Sensitivity to small errors:** PDDL domains are described as "extremely sensitive to errors — even minor inaccuracies in action preconditions can break planning". The proposed fix is environment-interaction feedback (the "Exploration Walk" metric), which reached a 66% average solve rate. — [search summary of Mahdavi et al., arXiv:2407.12979](https://alphaxiv.org/overview/2407.12979v2) (secondary)
- **Dynamic constraints:** one 2026 paper (venue and date not verified) reports that PDDL2.1 formalization collapses when constraints change mid-execution (PDDL2.1 0.7%, CP-SAT 46.1%, Planner 23.9%). A state-aware repair that updates only event-affected constraints brings the CP-SAT formalizer back to 84.5%. — [awesomepapers.io entry 2606.00981](https://awesomepapers.io/papers/2606.00981) (aggregator; treat as unverified)

### Inferences
- For a harness, the transferable design pattern is *propose → independent sound check → structured rejection feedback → bounded retry*. The guarantee is exactly "accepted outputs satisfy the checked constraints", which is no stronger than verifier soundness and coverage. This matches Warranted's "gate with independently checked evidence" invariant.
- An LLM acting as critic is a heuristic signal, not a gate: its false-acceptance rate (38/100 in one study) is incompatible with acceptance semantics.
- Formalization (NL→PDDL/constraints) moves the trust question to the translation step. A harness needs a separate way to validate the formal model, such as fidelity tests, round-trip checks, or human review, before a solver result means anything about the user's intent.
- Choice of formal target matters. Constraint programming (CP-SAT) appears more robust than PDDL under changing constraints in at least one report.

### Gaps
- No 2025–2026 large-scale replication of LLM-Modulo beyond scheduling benchmarks was found. Numbers for Blocksworld improvement with verifier feedback (an "82% in 15 rounds" figure in a third-party write-up) were not confirmed in a primary source.
- Exact per-benchmark numbers from arXiv:2411.14484 were not extracted.
- Nothing was found on how frontier 2026 reasoning models change the "LLMs can't plan/self-verify" conclusion. This needs a targeted search.

---

## 2. LLM + logic programming / Datalog / Prolog / ASP / probabilistic logic / SMT

### Takeaway
"LLM translates, solver infers" (Logic-LM, LINC, SatLM) gave large gains on 2023-era deductive-reasoning benchmarks. The dominant failure is translation (syntax errors, dropped implicit premises), and results vary heavily with solver choice. Differentiable or probabilistic logic programming (Scallop → Lobster, DeepProbLog lineage) is a separate, training-time family whose 2025–2026 progress is mainly performance (GPU execution).

### Cited Findings
- **Logic-LM (Pan et al., 2023, arXiv:2305.12295):** LLM → symbolic formulation → deterministic solver, with a self-refinement stage driven by solver error messages. Reported average gains of 62.6% over standard prompting and 23.5% over chain-of-thought. — [arXiv:2305.12295](https://arxiv.org/abs/2305.12295v1); [emergentmind summary](https://www.emergentmind.com/topics/logic-lm)
- Logic-LM's own limitation statement: success depends on the LLM producing correct formulations, and self-refinement depends on informative solver error messages. — [project summary](https://www.sourcepulse.org/projects/2335841) (secondary)
- **LINC (Olausson et al., 2023):** generates multiple NL→FOL translations and uses k-majority voting over prover outcomes to mitigate translation errors. — [LOGICPO paper's related work, arXiv:2506.18383](https://arxiv.org/pdf/2506.18383)
- **Error profile on FOLIO (GPT-3.5, annotation from the LOGICPO paper):** Logic-LM had 18 implicit-information-loss, 20 explicit-information, **84 syntax**, and 4 wrong-translation errors. LINC had 22 / 13 / 36 / 1. Malformed output dominates for Logic-LM; dropped implicit content is relatively larger for LINC. — [arXiv:2506.18383](https://arxiv.org/pdf/2506.18383)
- **Solver choice (ALTA 2024 study):** up to 50% performance variation per LLM across tools. LLMs translate most easily for Prover9, then Z3, then Pyke. Prover9 had the smallest gap between execution rate and accuracy. — [ACL Anthology 2024.alta-1.4](https://aclanthology.org/2024.alta-1.4.pdf)
- Follow-ons: CLOVER (compositional translation via atomic sentences with logical dependency structure) and VERUS-LM (self-refinement from reasoning-engine feedback). — [arXiv:2410.08047](https://arxiv.org/html/2410.08047v2)
- **SatLM (Ye et al., 2023):** the LLM writes a declarative specification and an SMT/SAT solver derives the answer. No verified numbers were retrieved in this session. — [mentioned in arXiv:2410.08047](https://arxiv.org/html/2410.08047v2)
- **Lobster (UPenn, Naik group; ASPLOS 2026):** compiles a Scallop-style Datalog neurosymbolic language to a new IR (APM) that runs on GPU. It supports discrete, probabilistic, and differentiable reasoning through provenance semirings. Average speedup over Scallop is 3.9× across 9 applications (v2, Sep 2025; v1 reported 5.3× across 8). — [arXiv:2503.21937v2](https://arxiv.org/abs/2503.21937v2)
- **XLOG (arXiv 2609.27203, Sep 2026):** a CUDA-native neurosymbolic engine adding exact knowledge compilation and epistemic reasoning. It names Lobster as the most directly comparable system. — [arXiv:2609.27203](https://arxiv.org/pdf/2609.27203)
- **Broader theoretical caution:** a late-2025 paper on fundamental limits of LLMs at scale was returned in the same search space. Its relevance to symbolic offloading was not examined. — [arXiv:2511.12869](https://arxiv.org/pdf/2511.12869)

### Inferences
- The solver result is sound *relative to the formalization*. The harness-relevant distinction is between "solver says entailed", "translation failed to parse/execute", and "translation possibly unfaithful". These should be distinct outcomes, not collapsed into right/wrong. This parallels Warranted's rejected/unproved/unsupported/unknown distinctions.
- Majority voting over several translations (LINC) is a cheap ensemble pattern for detecting ambiguity: disagreement among translations is evidence the input is underspecified.
- Solver error messages are the feedback channel for repair loops. Which solver is chosen changes how well that loop works (Prover9 vs Z3 vs Pyke).
- Scallop/Lobster/DeepProbLog-style differentiable logic is mostly relevant to *training* neural perception under logical constraints, not to runtime agent harnesses. Provenance semirings, however, are a well-founded way to carry "why/how-derived" metadata through rule evaluation.

### Gaps
- No 2025–2026 head-to-head of Logic-LM/LINC/SatLM with frontier reasoning models was found. It is unclear whether solver offloading still beats native long-chain reasoning on FOLIO/ProofWriter-class benchmarks.
- ASP-specific LLM pipelines and DeepProbLog lineage papers were not retrieved in this session.
- SatLM's reported accuracy figures were not verified.

---

## 3. Program-as-plan / code-as-action, typed and grammar-constrained interfaces

### Takeaway
Making the agent's action a program run by an interpreter (CodeAct, PoT, ViperGPT) or making the pipeline a typed program with optimizable LM modules (DSPy) gives measured gains and moves control and data flow into an executable, inspectable artifact. Hard format constraints (constrained decoding) guarantee syntax but can degrade reasoning. The recommended pattern is "reason freely, then convert to the format".

### Cited Findings
- **CodeAct (Wang et al., ICML 2024):** agents emit executable Python instead of JSON/text tool calls, and can revise actions over multiple turns from observations. Across 17 LLMs on API-Bank and a new benchmark (M3ToolEval), it is up to 20 percentage points higher in success rate with up to 30% fewer actions. The gains are attributed to control flow and data flow, and the benefit grows with model capability. — [PMLR v235](https://proceedings.mlr.press/v235/wang24h.html); [arXiv:2402.01030](https://arxiv.org/html/2402.01030v4)
- Secondary per-model figures (GPT-4 74.4% vs 53.7% text actions on M3ToolEval) were not verified against paper tables. — [beancount.io research log](https://beancount.io/bean-labs/research-logs/2026/04/29/codeact-executable-code-actions-llm-agents) (secondary)
- **DSPy (Khattab et al., 2023; NeurIPS 2023 workshop, later ICLR 2024):** pipelines are imperative computation graphs of declarative LM modules (signatures). A compiler with "teleprompters" bootstraps demonstrations or fine-tunes against a metric. Reported gains over standard few-shot prompting are >25% (GPT-3.5) and >65% (llama2-13b-chat), and 5–46% / 16–40% over expert-written demonstrations. Program flow is separated from parameters (prompts/weights). — [arXiv:2310.03714](https://arxiv.org/pdf/2310.03714); [Berkeley Sky project page](https://sky.cs.berkeley.edu/project/dspy/)
- **"Let Me Speak Freely?" (Tam et al., EMNLP 2024 Industry):** format restrictions such as JSON mode and constrained decoding significantly reduce reasoning performance. Stricter constraints cause larger drops, while classification can improve. Parsing failures are not the main cause. Free-form reasoning followed by NL→format conversion mitigates this. — [arXiv:2408.02442](https://arxiv.org/pdf/2408.02442); [ACL Anthology](https://preview.aclanthology.org/credits/2024.emnlp-industry.91)
- Third-party note: the study did not test many models or very strong ones. — [AI Made Simple summary](https://smarttechinvest.beehiiv.com/p/the-impact-of-format-restrictions-on-large-language-models) (secondary)

### Inferences
- Code-as-action fits a harness that gives workers shell/file capabilities. The program, its stdout/stderr, and exit status are mechanically capturable provenance. Executing a program does not establish that it is correct, though, so acceptance still needs separate checks.
- Typed and grammar-constrained interfaces belong at *state and trust boundaries*: structured receipts, job specs, and checker outputs. Imposing them on the model's reasoning trace may hurt quality. A two-phase pattern (free reasoning, then a typed, validated submission) is supported by Tam et al.
- DSPy-style compilation is an optimizer over prompts and weights against a metric. The metric is the checker, so the usual train/dev/held-out separation and the risk of optimizing against a weak metric apply directly.

### Gaps
- Primary sources were not retrieved this session for Program-of-Thoughts ([arXiv:2211.12588](https://arxiv.org/abs/2211.12588)), ViperGPT ([arXiv:2303.08128](https://arxiv.org/abs/2303.08128)), LMQL, Outlines, or DSPy Assertions. Their measured claims are not included here.
- No evidence was found on whether the constrained-decoding penalty persists for 2025–2026 reasoning models that think before emitting structured output.

---

## 4. Neurosymbolic memory and knowledge: KGs, ontologies, truth maintenance, premise dependency tracking

### Takeaway
Deployed agent-memory systems have adopted *temporal knowledge graphs* with fact validity intervals (Zep/Graphiti), which is a partial, non-logical form of belief revision. No 2025–2026 LLM-agent system implementing a classical truth-maintenance system (justification networks, dependency-directed retraction) was found. The TMS idea remains a design resource rather than an implemented agent pattern in the literature retrieved.

### Cited Findings
- **Zep / Graphiti (arXiv:2501.13956, Jan 2025):** a temporally-aware KG engine combining conversational and structured business data while keeping history. It uses a bi-temporal model (event time T and ingestion/transaction time T′), and facts carry creation/expiration/validity timestamps so outdated facts are invalidated rather than deleted. The hierarchy has episodic, semantic, and community subgraphs. Reported results: 94.8% vs MemGPT 93.4% on DMR, and up to 18.5% accuracy improvement with 90% lower latency on LongMemEval. — [arXiv:2501.13956](https://arxiv.org/html/2501.13956v1); [Zep blog](https://blog.getzep.com/zep-a-temporal-knowledge-graph-architecture-for-agent-memory); [Moonlight review](https://themoonlight.io/review/zep-a-temporal-knowledge-graph-architecture-for-agent-memory) (secondary for validity-field details)
- These benchmark numbers are vendor-authored. No independent replication was found.
- **Truth maintenance background:** a TMS records beliefs and their justifications in a dependency network. Premises need no justification, and every other belief traces back to premises. Contradictions trigger dependency-directed backtracking to the responsible assumptions. A TMS tracks lineage alongside an inference engine without generating inferences itself. — [Wikipedia: Reason maintenance](https://en.wikipedia.org/wiki/Reason_maintenance); [Shapiro, belief revision overview](https://cse.buffalo.edu/faculty/shapiro/Papers/br-overview.pdf)
- Belief revision is motivated where assertions come from multiple sources that may contradict each other. — [Shapiro overview](https://cse.buffalo.edu/faculty/shapiro/Papers/br-overview.pdf)
- A search for 2025 LLM-agent TMS/belief-revision work returned only classical literature. — (negative search result; see Gaps)

### Inferences
- The separation a TMS makes (the inference engine proposes, a bookkeeping layer records justifications and retracts dependents when a premise is retracted) maps directly onto "LLM worker proposes, host records premises and invalidates dependent applications". This is Warranted's invariant 2 (proof validity vs current applicability). Temporal KG validity intervals give "when was this believed". They do not give "what depends on this", which is the part Warranted needs for stale-dependency rejection.
- Provenance semirings (Section 2, Lobster/Scallop) are a formal tool for propagating derivation lineage. They could inform a dependency-tracking design even without differentiable reasoning.

### Gaps
- No 2025–2026 primary source for a TMS/ATMS-backed LLM agent was found. This could reflect search limits rather than absence, so a targeted arXiv search ("assumption-based truth maintenance LLM", "belief revision LLM agent") is recommended.
- Ontology-grounded agents (OWL/SHACL validation in agent loops) and GraphRAG were not covered in this session.

---

## 5. Cognitive-architecture-inspired agents and 2025–2026 industry neurosymbolic products

### Takeaway
CoALA (TMLR 2024) is the standard conceptual frame: modular memory, internal and external actions, and a decision loop. It is descriptive, not an enforcement mechanism. In industry, the clearest shipped neurosymbolic *verification* products are AWS Automated Reasoning checks (GA Aug 2025) and Imandra CodeLogician (2025, paper 2026). Both follow "LLM formalizes, solver decides". Little current, verifiable information was found on Elemental Cognition, Symbolica, or IBM agent products.

### Cited Findings
- **CoALA (Sumers, Yao, Narasimhan, Griffiths, TMLR 2024):** working memory (context/prompt) plus long-term episodic, semantic, and procedural memory. External (grounding) actions are separated from internal actions (retrieval, reasoning, learning = writes to long-term memory). In a decision cycle, planning via reasoning and retrieval selects one grounding or learning action. — [arXiv:2309.02427](https://arxiv.org/pdf/2309.02427); [TMLR listing](https://mlanthology.org/tmlr/2024/sumers2024tmlr-cognitive)
- **Amazon Bedrock Guardrails Automated Reasoning checks:** GA in August 2025 in US East/West and EU regions (preview Dec 2024). AWS claims "up to 99% verification accuracy" at detecting correct responses, which is a ceiling claim, and no independent benchmark was found. Policies encode domain rules in formal logic, and responses are checked against them with formal verification. — [AWS What's New, Aug 2025](https://aws.amazon.com/about-aws/whats-new/2025/08/automated-reasoning-checks-amazon-bedrock-guardrails); [AWS News Blog](https://aws.amazon.com/blogs/aws/minimize-ai-hallucinations-and-deliver-up-to-99-verification-accuracy-with-automated-reasoning-checks-now-available/)
- AWS states the checks do **not** protect against prompt injection, since they validate whatever content they receive. They should be paired with content filters. — [search summary of AWS Bedrock user guide](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-automated-reasoning-checks.html) (page fetch failed; claim from search snippet)
- **Imandra CodeLogician:** announced 26 Mar 2025 as a LangGraph agent that turns source code into formal models reasoned about by ImandraX (Python first; Java and COBOL planned). Uses include bug finding, property validation, state-space exploration, and test generation. — [Imandra announcement](https://www.imandra.ai/articles/imandra-releases-codelogician); [DevOps.com](https://devops.com/imandra-extends-symbolic-ai-model-to-validate-source-code/)
- CodeLogician paper (arXiv:2601.11840, Jan/Feb 2026): its stated design difference is using LLMs to *construct explicit formal models* of software rather than using formal methods only to validate LLM outputs. It introduces code-logic-bench and reports that formal augmentation closes a 41–47 percentage-point reasoning-accuracy gap. Imandra now positions it as an "agentic governance platform for AI coding". — [arXiv:2601.11840](https://arxiv.org/abs/2601.11840v2); [Imandra product page](https://www.imandra.ai/codelogician)
- **Elemental Cognition:** a hybrid platform in which LLMs handle natural-language interaction and a separate reasoning/problem-solving engine computes answers. Products include Cogent and Cora chatbots. It raised about $60M in 2023. Founder David Ferrucci reportedly left later (date unverified). — [SiliconANGLE, Aug 2023](https://siliconangle.com/2023/08/17/elemental-cognition-led-ibm-watsons-former-head-raises-60m/); [Yahoo/Fortune piece](https://tech.yahoo.com/ai/articles/generative-ai-t-shake-reliability-070000386.html)
- **Agent-architecture survey:** a reported Jan 2026 systematic survey of 178 papers (2020–2025) proposing a taxonomy of neuro-symbolic agent architectures (ScienceDirect) is known only through a blog. — [Zylos blog](https://zylos.ai/research/2026-03-21-neuro-symbolic-ai-agent-reasoning) (unverified)
- **Broader NeSy survey:** Mättas, Järv & Tammet cover 2020–2025 advances under performance, understandability, reliability, and ethics. — [Neurosymbolic AI journal paper 933](https://neurosymbolic-ai-journal.com/system/files/nai-paper-933.pdf)

### Inferences
- The AWS and Imandra products, and Elemental Cognition's design, share one structure. A domain policy or formal model is authored, with LLM assistance and human review. At runtime the LLM translates inputs into that formal vocabulary, and a solver returns a verdict. The trust-critical artifacts are the reviewed policy/model and the translation, not the LLM answer. A domain-independent harness can treat "formal policy + translator + solver" as one kind of *checker* behind its checker interface rather than as core semantics.
- The AWS prompt-injection caveat shows that a formal check on content does not secure the channel. Isolation and provenance boundaries remain separate requirements.
- CoALA's split between internal and external actions, and its "learning = write to long-term memory" category, give vocabulary for distinguishing candidate state from authoritative state. CoALA itself specifies no acceptance or provenance discipline.

### Gaps
- The Bedrock user guide could not be fetched. Details of AR-check result categories (from memory, possibly VALID / INVALID / SATISFIABLE / IMPOSSIBLE / TRANSLATION_AMBIGUOUS / TOO_COMPLEX / NO_TRANSLATIONS), policy-building workflow, and multi-translation ambiguity detection are **unverified** and should be checked against the AWS docs before use.
- No current information was found on Symbolica's products, IBM's 2025–2026 neurosymbolic agent offerings (e.g., in watsonx), or Soar/ACT-R-integrated LLM agents. These need dedicated searches.
- Imandra "Universe" was mentioned on its site but not explained in the retrieved sources.

---

## 6. Empirical evidence: where symbolic components improve reliability and where they fail

### Takeaway
The evidence consistently shows gains when (i) a *sound* external checker gates outputs (planning, scheduling, code properties) and (ii) the problem is cleanly formalizable. Failures cluster in the NL→formal translation step: syntax errors, dropped implicit premises, sensitivity to small model errors, degradation on naturalistic text, and changing constraints. They also cluster in coverage, since a checker certifies only what it encodes. Vendor accuracy claims ("up to 99%") lack independent replication.

### Cited Findings
- Gains with sound external verification: LLM-Modulo scheduling gains ([arXiv:2411.14484](https://arxiv.org/html/2411.14484v1)); LLM+P's optimal plans vs infeasible LLM plans ([papers.cool](https://papers.cool/arxiv/2304.11477)); Logic-LM +62.6% / +23.5% ([arXiv:2305.12295](https://arxiv.org/abs/2305.12295v1)); CodeLogician closing a 41–47 pp gap on its own benchmark ([arXiv:2601.11840](https://arxiv.org/abs/2601.11840v2)); CodeAct up to +20 pp ([PMLR](https://proceedings.mlr.press/v235/wang24h.html)).
- LLM-as-verifier fails: 38/100 false positives in Blocksworld self-critique ([arXiv:2310.08118](https://arxiv.org/pdf/2310.08118)).
- Translation brittleness: syntax errors dominate Logic-LM failures on FOLIO ([arXiv:2506.18383](https://arxiv.org/pdf/2506.18383)); up to 50% variation by solver choice ([ALTA 2024](https://aclanthology.org/2024.alta-1.4.pdf)); PDDL formalization degrades with natural phrasing ([arXiv:2412.09879](https://arxiv.org/html/2412.09879v4)); minor precondition errors break planning ([alphaxiv overview of 2407.12979](https://alphaxiv.org/overview/2407.12979v2)); PDDL2.1 collapses to 0.7% under dynamic constraints in one unverified report ([awesomepapers.io](https://awesomepapers.io/papers/2606.00981)).
- Interface constraints can cost quality: strict structured decoding lowers reasoning accuracy ([arXiv:2408.02442](https://arxiv.org/pdf/2408.02442)).
- Coverage and channel limits: formal checks do not cover prompt injection ([AWS blog](https://aws.amazon.com/blogs/aws/minimize-ai-hallucinations-and-deliver-up-to-99-verification-accuracy-with-automated-reasoning-checks-now-available/); AWS docs via search snippet). "Guaranteed correct" holds only relative to verifier soundness and completeness ([arXiv:2411.14484](https://arxiv.org/html/2411.14484v1)).

### Inferences: harness-level patterns a domain-independent harness could adopt
1. **Generator/checker separation with a sound, independent checker as the gate.** An LLM critic may advise but never accepts.
2. **Typed outcome lattice for checker results.** Accepted, rejected-with-counterexample, translation-failed (parse/execute), translation-ambiguous (ensemble disagreement), too-complex/timeout, and unsupported are distinct, and none defaults to success.
3. **Formal model as a reviewed, versioned artifact.** The PDDL domain, policy, or formal model is authored once with LLM help, reviewed, and pinned by version. Runtime translations are checked against it, and changes are authorized revisions.
4. **Translation ensembles** (LINC-style k-way translation and voting) to detect ambiguity before trusting a solver verdict.
5. **Solver-feedback repair loops** with bounded retries and recorded attempts (Logic-LM self-refine, LLM-Modulo backprompting). Error-message quality is a design parameter.
6. **Code-as-action workers** with captured execution provenance, plus free-form reasoning before typed submission at trust boundaries.
7. **Dependency-tracked premises** (TMS-style justifications) rather than only temporal validity, so retracting a premise invalidates dependent applications.
8. **State scope honestly.** A passed formal check certifies only the encoded properties under the given translation.

### Gaps
- No controlled 2025–2026 study was found that measures the *net* reliability benefit of symbolic offloading for frontier reasoning models across many domains. Much of the strongest evidence uses GPT-3.5/GPT-4-era models.
- No independent evaluation of AWS Automated Reasoning checks' 99% claim, or of their false-negative and translation-failure rates, was found.
- The cost and latency overheads of verifier loops (number of LLM calls per accepted plan) were not extracted from the primary papers.
