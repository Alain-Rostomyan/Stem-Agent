# Stem Agent — Project Briefing

This document is the full context for the project. Read it end-to-end before starting work.

---

## 1. The Assignment (verbatim)

> A stem cell doesn't know what it will become. It reads signals from its environment and transforms — into a neuron, a muscle fiber, a blood cell. Whatever the body needs. If something goes wrong mid-transformation, built-in safeguards pull it back. What if AI agents worked the same way? Today, we hand-build AI agents for specific tasks — wire up the prompts, pick the tools, design the harness. The agent works, but it only works for what we built it to do. What if instead, we started with a minimal stem agent — one that takes a class of problems (Deep Research, Quality Assurance, Security, etc.), figures out how they're solved, and grows into a specialized agent on its own? That's the challenge: build a stem agent. How might it figure out the way a given type of task is typically approached? How does it decide what to become — what architecture, what tools, what skills and how to obtain them? How does it rebuild itself without breaking along the way? And how does it know when it's good enough to stop evolving and start executing? The end result isn't a universal agent. It's an agent that became specific — through its own process. For a different class of tasks, you'd start a new stem agent.
>
> Scope, task domain, architecture, and evaluation method are yours to choose. What you choose and why is part of what we evaluate.
>
> **Deliverables**
> - Working, runnable code with setup instructions
> - Measurable before/after comparison on your chosen evaluation
> - Write-up (up to 4 pages): your approach, experiments, what surprised you, what failed, what you'd do with more time
>
> **How we evaluate**
> They read the write-up *before* they look at the code. They care about the path of thinking — especially where things didn't go as expected.

LLM access: OpenAI API key requested from JetBrains.

---

## 2. Interpretation

There are two agents in the story:

1. **The stem** — short-lived, domain-agnostic. Its job is to investigate a problem class, decide what kind of specialized agent is needed, build that agent, and stop when it's good enough.
2. **The specialized agent** — the stem's output. This is what actually solves user tasks at evaluation time.

The stem is generic; the specialized agents it produces are different for different domains. A different domain → a fresh stem run → a different specialized agent.

The four design questions in the prompt map to four concrete decisions:

- *How does it figure out how a task is typically approached?* → The stem's **investigation** phase: read sample tasks, reason about the domain, possibly try a baseline solve and observe friction.
- *How does it decide what to become?* → The **proposal** phase: pick tools to enable, write a system prompt, optionally write new tools, optionally pick an architecture (single-loop vs planner+executor).
- *How does it rebuild without breaking?* → **Safeguards**: every change is committed via git; every change is sanity-checked against a trivial probe task; failed changes are rolled back.
- *How does it know when to stop?* → **Stopping criterion**: probe-set score plateau across N iterations OR budget exhausted, whichever first.

---

## 3. Design Decisions (locked)

### Architecture: middle route
The stem starts with a curated set of pre-built tools AND has the ability to write its own Python tools. Not pure tool synthesis from scratch (too slow within the time budget), not fixed-tool selection only (too unambitious for the spirit of the prompt).

**Starter tools (~7):**
- `read_file(path)`
- `write_file(path, content)`
- `list_directory(path)`
- `run_shell_command(cmd)`
- `run_python(code)`
- `web_search(query)`
- `call_llm(prompt)` — for sub-LLM calls inside the agent

These are deliberately generic. Nothing in this list pre-decides what kind of QA or Research agent the stem will become.

### Stem output artifact: a config file
The stem produces a JSON config describing the specialized agent. The config has:
```
{
  "system_prompt": "...",
  "enabled_tools": ["read_file", "run_python", ...],
  "custom_tools": ["tools/custom/extract_test_targets.py", ...],
  "few_shot_examples": [{...}, {...}],
  "architecture": "single_loop" | "planner_executor",
  "domain_metadata": {"name": "qa", "notes": "..."}
}
```
At eval time, `load_config(path)` instantiates a runnable specialized agent. Configs are inspectable and diffable across domains, which makes the writeup figures easy.

### Evaluation domains
**Two domains**, chosen for shape diversity:

1. **QA — Python test generation.** Given a Python repo and possibly a known buggy function, the agent writes tests that catch the bug. Eval: SWE-bench-lite subset (~15–20 tasks) or curated repos with seeded bugs. Metric: pass rate / did-tests-catch-bug.
2. **Deep Research — question answering with citations.** Given a question, the agent searches the web, gathers sources, produces an answer with citations. Eval: SimpleQA or FreshQA subset (~20 questions). Metric: factual accuracy (LLM-judge) + citation validity.

**Plus one ablation:** B-style stem with only `run_python` and `call_llm` (no other starter tools). Run on QA only. Qualitative analysis: what tools did it bootstrap, how far did it get, where did it stall.

### Baseline ("before")
For each domain: a **generic agent** with the same starter tools and a generic system prompt ("you are a helpful agent, solve this task"). Same LLM, same call budget per task. This isolates the value of specialization specifically.

### Stopping criterion
Probe-set score plateau (delta < 5% across 3 iterations) OR LLM call budget exhausted. Whichever first. Simple and defensible.

### Safeguards (rebuild without breaking)
- Every evolution step = one git commit on the config + tools.
- Before committing, run a sanity check: a trivial probe task that any working agent should pass. If sanity check fails → `git reset --hard HEAD~1` and try a different proposal.
- LLM call budget hard-enforced at the wrapper level. No infinite loops.

---

## 4. Project Structure

```
stem-agent/
├── stem/
│   ├── __init__.py
│   ├── llm_client.py          # OpenAI wrapper with logging + budget enforcement
│   ├── investigate.py         # stem's investigation phase
│   ├── propose.py             # stem proposes config changes
│   ├── test_proposal.py       # runs probe set, returns score
│   ├── commit.py              # git commit / rollback logic
│   ├── stop_check.py          # plateau detection + budget check
│   └── run_stem.py            # main loop
├── agent/
│   ├── __init__.py
│   ├── config.py              # config schema, load_config
│   ├── runner.py              # instantiate and run a specialized agent from config
│   └── baseline.py            # generic baseline agent
├── tools/
│   ├── starter/               # the 7 starter tools
│   │   ├── read_file.py
│   │   ├── write_file.py
│   │   ├── list_directory.py
│   │   ├── run_shell_command.py
│   │   ├── run_python.py
│   │   ├── web_search.py
│   │   └── call_llm.py
│   ├── custom/                # tools the stem writes (one subdir per domain)
│   │   ├── qa/
│   │   └── research/
│   └── registry.py            # tool registration + introspection
├── evals/
│   ├── qa/
│   │   ├── data/              # eval tasks
│   │   ├── probe_set.json     # small held-out set used during stem evolution
│   │   ├── eval_set.json      # final eval set (never seen by stem)
│   │   └── runner.py          # eval runner for QA
│   └── research/
│       ├── data/
│       ├── probe_set.json
│       ├── eval_set.json
│       └── runner.py
├── configs/                   # final configs produced by stem runs
│   ├── qa.json
│   ├── research.json
│   └── qa_ablation.json
├── runs/                      # logs from stem evolution runs
├── writeup/
│   └── writeup.md
└── README.md
```

---

## 5. Step-by-Step Plan

### Phase 0 — Setup
1. Initialize repo, virtualenv, project structure above. `git init`.
2. Build `llm_client.py`: thin wrapper around OpenAI. Logs every call (prompt, response, tokens, cost). Enforces a per-run call budget (param: `max_calls`). Raises `BudgetExceeded` when hit.
3. Build the config schema in `agent/config.py`. JSON. Include `load_config(path)` that returns an instantiated agent.
4. Build the 7 starter tools in `tools/starter/`. Each is a Python function with a clear signature and a docstring (the docstring is what the LLM sees). Build `tools/registry.py` to register/list tools and produce LLM-readable tool descriptions.
5. Build `agent/runner.py`: takes a config, instantiates a specialized agent (system prompt + tools + optional architecture), runs it on a task, returns the result. Support both `single_loop` and `planner_executor` architectures.
6. Build `agent/baseline.py`: generic agent with all starter tools, generic system prompt, single loop. This is the "before."
7. Smoke test: run baseline on a dummy task end-to-end. Confirm logging works, tool calls work, budget enforcement works.

### Phase 1 — Eval infrastructure
8. **QA eval data.** Try SWE-bench-lite first. If too heavy, curate 15–20 tasks. Alternative: pick 10 small Python repos, seed bugs via mutation, write ground-truth tests that catch the bugs.
9. Split QA data: ~5 tasks for the stem's probe set (used during evolution), ~15 tasks for the final eval set (never shown to stem).
10. Build `evals/qa/runner.py`: takes a config + task list, runs the agent on each, scores it, returns aggregate metric.
11. **Research eval data.** SimpleQA or FreshQA subset, 20 questions with verifiable short answers. Split: 5 probe, 15 eval.
12. Build `evals/research/runner.py`: same shape as QA runner but with LLM-judge scoring. Document the judge prompt.
13. Run the baseline on both eval sets. **Lock these numbers in** as the "before" baseline. Save to `runs/baseline_qa.json` and `runs/baseline_research.json`.

### Phase 2 — Stem core
14. Build `stem/investigate.py`: takes a domain description + sample tasks (from probe set), produces a written analysis ("this domain requires X, agents typically do Y, useful tools would be Z"). One LLM call. Output saved to run log.
15. Build `stem/propose.py`: takes the current config + investigation analysis + recent probe results, proposes a structured change. Output is a JSON object: `{"action": "modify_prompt" | "enable_tool" | "write_tool" | "add_few_shot" | "switch_architecture", "details": {...}}`. Constrain output format strictly (use OpenAI's JSON mode or a strict schema in the prompt).
16. Build `stem/test_proposal.py`: applies the proposed change to a temp copy of the config, instantiates the agent, runs it on the probe set, returns score + sanity check pass/fail. The sanity check is a trivial task (e.g., for QA: "does the agent successfully read a file we know exists"). If sanity fails, the proposal is rejected regardless of score.
17. Build `stem/commit.py`: if proposal accepted, write the change, `git add`, `git commit -m "evolution step N: <action>"`. If rejected, `git reset --hard HEAD` (no change to commit yet, but make sure no dirty state).
18. Build `stem/stop_check.py`: tracks last N probe scores. Returns True if delta < threshold for N iterations, OR if budget hit.
19. Build `stem/run_stem.py`: the main loop. Initialize empty config → investigate → loop {propose, test, commit-or-rollback, stop-check} → save final config.

### Phase 3 — Runs
20. Run stem on QA. Budget: ~50 LLM calls for evolution (tunable). Watch first 2–3 iterations for bugs. Save final config to `configs/qa.json`. Save run log to `runs/stem_qa_<timestamp>.json`.
21. Run the resulting specialized QA agent on the eval set. Record the "after" number.
22. Run stem on Research. Lighter monitoring since the machinery is proven. Save config + log.
23. Run the specialized Research agent on its eval set. Record.
24. Run the **B-style ablation**: stem with starter tools restricted to `run_python` and `call_llm` only. Domain: QA. Same budget. Save config + log. Note especially: which tools did it write itself, how many iterations did it survive, did it ever pass sanity checks, what was its eval score (if any).

### Phase 4 — Analysis and writeup
25. Diff `configs/qa.json` against `configs/research.json`. Use this diff as a figure in the writeup ("same stem, different organisms").
26. Inspect 2–3 custom tools the stem wrote per domain. Pick interesting ones for the writeup (especially anything you wouldn't have written by hand).
27. Comb run logs for surprises: rejected proposals, rollbacks, places the stem repeated itself, places it converged faster than expected.
28. Write the 4-page writeup. Structure:
    - **Approach** (1 page): two-agent framing, design choices (middle route, config-as-artifact, two domains, generic-baseline comparison), rationale.
    - **Results** (1.5 pages): before/after table for both domains. Config diff figure. Highlight 1–2 custom tools the stem wrote.
    - **What surprised / what failed** (1 page): rollbacks, ablation findings, anything unexpected.
    - **What I'd do with more time** (0.5 page): scaling to more domains, deeper tool synthesis, learned stopping criteria, etc.
29. Write README with setup instructions: `git clone && pip install -r requirements.txt && export OPENAI_API_KEY=... && python -m stem.run_stem --domain qa`. Include expected runtime and approximate API cost.
30. Final validation pass: re-run eval on saved configs to confirm numbers in writeup match. Tag commit. Done.

---

## 6. Anti-scope (do NOT do these)

- Do not make the stem multi-step-reasoning fancy. One LLM call per propose step.
- Do not build a UI.
- Do not implement recursive self-improvement (stems that grow stems).
- Do not run more than 2 domains for the main eval. Three is a trap.
- Do not keep tweaking the starter tool library after Phase 0 is done. Lock it.
- Do not let the stem touch production code outside `tools/custom/<domain>/` and `configs/<domain>.json`. Sandbox the changes.
- Do not skip the sanity check. It's the entire safeguard story.
- Do not try to do `web_search` from inside test runs without rate-limiting. Cache results.

---

## 7. Open questions to resolve while building

These are decisions that don't need to be made up front but should be settled before the relevant phase:

- **Probe set size.** Started at ~5 per domain. May need to grow if probe scores are too noisy to detect plateaus.
- **LLM call budget per stem run.** Started at 50. Tune based on cost and convergence speed.
- **Per-task call budget at eval time.** Should be the same for baseline and specialized agent for fairness. Suggest 20.
- **LLM choice.** GPT-4-class for the stem itself (reasoning quality matters). Could use a cheaper model for the specialized agent's per-task calls if cost is an issue, but keep it consistent across baseline and specialized for fairness.
- **Custom tool sandboxing.** Stem-written tools should run in a subprocess with a timeout. Don't let a hallucinated infinite loop kill the run.

---

## 8. What the writeup needs to argue

The graders read the writeup before the code, so the writeup has to stand alone. The thesis structure should be:

1. "I built a stem agent (middle route) that, given a problem class, produces a domain-specialized agent."
2. "I demonstrated it on two domains with different shapes — QA and Deep Research — and the stem produced different specialized agents for each, with measurable improvement over a generic baseline."
3. "Here are the things that surprised me, the things that failed, and what the ablation taught me about the limits of self-bootstrapping."
4. "Here's what I'd build next."

The grading rubric explicitly weights "the path your thinking took, especially where things didn't go as expected." Failure analysis is not optional — it is the centerpiece. Document rollbacks, dead ends, and the ablation honestly.