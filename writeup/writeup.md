# Stem Agent — Writeup

*A meta-agent that turns a generic agent into a domain-specialized one,
and what we learned from running it on two domains plus an ablation.*

---

## 1. Approach

**Two agents.** The "stem" is short-lived and domain-agnostic. Given a
problem class, sample tasks, and the generic baseline's traces, it
produces a JSON config that instantiates a "specialized agent" whose job
is to actually solve user tasks. The stem's lifecycle is investigate →
{propose, test, accept-or-rollback, stop-check}; once it stops, the
config is frozen and the specialized agent is what the evaluator sees.

**Middle route on tools.** The stem starts with seven generic starter
tools (`read_file`, `write_file`, `list_directory`, `run_python`,
`run_shell_command`, `web_search`, `call_llm`). It can enable a subset,
modify the system prompt, add few-shot examples, switch architecture
(`single_loop` vs `planner_executor`), or write a brand-new Python tool.
Each proposal is **one** LLM call; the briefing explicitly forbids fancy
multi-step proposing because we want each step to be auditable.

**Config as artifact.** The stem's output is a JSON file:
`{system_prompt, enabled_tools, custom_tools, few_shot_examples,
architecture, domain_metadata}`. This makes specialization
inspectable and diffable; you can read the config side-by-side with
the writeup.

**Safeguards.** Every accepted iteration is a real `git commit` on
`master`; every rejected iteration triggers `git reset --hard HEAD`,
restoring the working tree. The probe-set evaluation gates acceptance:
an evolved config must score within `regression_tolerance` of the last
accepted score (default 0.0; ties are kept so the loop can explore
laterally). Stopping is plateau detection (delta < 0.05 over the last 3
accepted scores) or budget exhaustion. The whole repository becomes the
audit trail of how the agent grew up.

**Two domains, one ablation.**

- **Research:** synthesis QA — open-domain questions over a defined
  finite universe (e.g., "Of the 27 EU members, how many use the
  Euro?"). 15-task held-out eval; LLM judge scores each answer for
  factual equivalence to a canonical answer, plus citation-URL
  liveness. Three question shapes: `agg`, `filter`, `granular`.
- **QA:** seeded-bug Python — the agent gets a small Python function
  with one planted bug and writes a pytest file. We score by running
  pytest twice, against buggy and held-out fixed implementations.
  A task scores 1 iff buggy fails and fixed passes.
- **B-style ablation:** QA-only stem with starter tools restricted to
  `run_python` and `call_llm` (no `write_file`, no `web_search`, etc.).
  The briefing's hypothesis: this should force the stem to author
  custom tools.

**Cost discipline.** Agent on `gpt-4o-mini`, judge on `gpt-4o-mini`,
stem on `gpt-5.1`. Per-task max-steps capped at 12 (same for baseline
and specialized — fairness). Stem evolution capped at 25 LLM calls
during smoke runs. Total project cost across all runs in this writeup:
**$1.07**.

---

## 2. Results

### 2.1 Before/after table (v1 evals, n=15 per row)

| Run                              | Tools | Probe       | Eval          | Δ vs domain baseline |
|----------------------------------|-------|-------------|---------------|-----------------------|
| Research locked baseline         | 7     | —           | 0.267 (4/15)  | (anchor)              |
| **Research evolved**             | **5** | 0.600       | **0.600 (9/15)** | **+33.3 pt**       |
| QA locked baseline               | 7     | —           | 0.667 (10/15) | (anchor)              |
| **QA evolved**                   | **6** | 0.800       | **0.800 (12/15)** | **+13.3 pt**       |
| **QA ablation**                  | **2** | 0.800       | **0.267 (4/15)** | **−40.0 pt** vs QA   |

**Two notable structural findings.**

1. **Research evolved beat baseline with FEWER tools** (5 vs 7). The
   stem started from a tool-starved 4-tool initial config (no
   `web_search`, no `write_file`, no `run_shell_command`) and chose
   to enable only `web_search`. The lift is therefore prompt + few-shots
   + one tool — not "more tools."
2. **The B-ablation replicated the unrestricted QA config's probe
   score (0.800)** while *underperforming the baseline* on eval
   (0.267 vs 0.667 with all 7 tools). The 53-point probe→eval gap is
   unique to this run; both the unrestricted research and QA runs had
   probe and eval matching within a percentage point.

### 2.2 Per-shape research lift

The locked research baseline scored 4/5 on `agg` but **0/5 on `filter`
and 0/5 on `granular`** — the structurally hard shapes. The evolved
config scored 4/5, 3/5, 2/5 respectively. The lift came entirely from
filter and granular; agg was already near-ceiling for both configs.
This is the cleanest possible specialization story: the stem unlocked
shapes the generic agent literally could not solve.

### 2.3 Config diff (research → QA)

The same stem produced visibly different organisms:

| Field                  | Research                                 | QA                                       |
|------------------------|------------------------------------------|------------------------------------------|
| Enabled tools          | read, list, py, llm, **web_search**      | read, list, py, llm, **write_file, run_shell** |
| System-prompt theme    | "list-then-filter-then-count over the set" | "test-PUT-don't-mirror-buggy-output"    |
| Few-shot count         | 2 (research-style: count Muslim-majority) | 1 (binary search example)                |
| Architecture           | single_loop                              | single_loop                              |
| Custom tools           | 0                                        | 0                                        |

The same generic loop, given two different problem classes, walked
toward two different prompts and two different tool sets — exactly the
"different specialized agents" outcome the briefing wanted.

### 2.4 Notable specializations the stem authored

- **Research, on subtle attributes:** *"For linguistic / demographic
  / constitutional attributes, always cross-check at least two
  sources."* The investigation surfaced specific failure modes
  (NATO-founding-monarchies miscounted as 3 instead of 7; SA Bantu
  languages miscounted as 8 instead of 9), and the prompt encodes the
  cross-check discipline.
- **QA, on the buggy-output trap:** *"Never derive expected outputs by
  calling the buggy implementation."* This is non-obvious: an agent
  that calls the buggy code, observes its output, then writes a test
  that *matches* that output will silently certify the bug. The
  generic-baseline prompt has no reason to flag this; the specialized
  prompt explicitly warns against it.
- **Ablation, on a meta-failure:** the only `switch_architecture` of
  any run was here. Stem rationale (verbatim): *"prompt guidance
  alone hasn't induced the desired multi-step behavior… switching to
  a planner_executor architecture is the highest-leverage change."*
  Probe jumped 0.000 → 0.200 in one step.

### 2.5 Custom tools authored: zero

The briefing anticipates we'd inspect "2–3 custom tools the stem
wrote per domain." Across **all three runs**, the stem authored
**zero custom tools**. This held even in the ablation, which was
specifically designed to corner the stem into `write_tool` territory:
with `enable_tool` starved of candidates from step 0, the stem chose
`modify_prompt → switch_architecture → add_few_shot × 8` and never
once selected `write_tool`. We treat this as a finding, not a missing
experiment — see §3.

---

## 3. What surprised us / what failed

### 3.1 The proposer's effective preference order forecloses tool authoring

`write_tool` is in the menu; the prompt describes it; the validator
accepts well-formed tool code. But across 19 proposals (5 research,
4 QA, 10 ablation), `write_tool` was selected zero times. The
proposer's effective ordering appears to be:

> `enable_tool` → `modify_prompt` → `add_few_shot` → `switch_architecture` → `write_tool`

with `write_tool` functionally unreachable in practice. The ablation
confirmed this: even with `enable_tool` exhausted at step 0, the
proposer scrolled past `write_tool` and reached for `add_few_shot`
seven more times.

This was the briefing's most-anticipated outcome and we got the
opposite. Two interpretations consistent with the data:

1. **Cost asymmetry.** Authoring a working Python tool requires
   writing real code that has to import cleanly and handle edge cases.
   `add_few_shot` requires writing prose. Under our one-shot proposer,
   prose is cheaper to draft and almost always parses; tools are
   high-stakes. The proposer is risk-averse.
2. **Investigation framing.** Our investigation prompt lists
   `write_tool` as one of five action types but doesn't motivate it
   any more than the others. A revised prompt that explicitly
   identifies tool gaps in the analysis section might shift the
   ordering. We did not test this.

### 3.2 The proposer is blind to existing few-shots

In two unrelated runs, the proposer authored near-duplicate few-shots:

- **Research run:** two consecutive `add_few_shot` proposals about
  *"Of the 10 most populous countries, how many are Muslim-majority?"*
  with **contradicting canonical answers** (count=4 in one, count=3
  in the other). Both ended up in the final config.
- **Ablation run:** five consecutive `add_few_shot` proposals, all
  about `binary_search`. Same task, slightly different framing. The
  agent ended up with five binary-search examples and zero coverage
  of any other shape.

This is systemic: `propose.py` does not read the existing
`few_shot_examples` field when authoring a new one. It looks at the
investigation analysis, the recent probe scores, and the current
config's *summary*, but the few-shot bodies aren't surfaced. Easy fix
(include them in the propose prompt's context block); we didn't have
time to verify whether fixing it would change scores.

### 3.3 The ablation's eval collapse from a passing probe

The ablation scored 0.800 on the 5-question probe set (matching the
unrestricted QA run!) and 0.267 on the 15-question eval — *worse
than the 7-tool baseline* of 0.667. 11 of 15 ablation eval failures
are `no_test_file`: the agent simply never produced a test artifact
under the constrained toolset within `max_steps_per_task=12`. With
only `run_python` and `call_llm`, writing a test file is a multi-call
operation (compose Python, exec via run_python, file I/O), and the
planner_executor architecture eats steps in its plan stage before
the executor can land the file.

This is also a cautionary finding: **5 probe questions are too few
when the toolset is restricted.** The probe-eval gap on the
unrestricted runs was within a percentage point both times; here it
was 53 points. The probe set is the only signal the stem optimizes
against, and we now know it's noisy under tool constraints.

### 3.4 Single-seed variance is large on research

After completing all three evolutions, we re-ran the saved configs
on the same eval to verify reproducibility (briefing step 30).
Findings:

| Config             | First eval | Validation rerun | Δ          |
|--------------------|------------|-------------------|------------|
| Research evolved   | 0.600      | 0.333             | **−0.267** |
| QA evolved         | 0.800      | 0.800             | 0.0        |
| QA ablation        | 0.267      | 0.333             | +0.067     |

Identical config, identical eval, identical model, two runs minutes
apart. Research swung 27 points; QA was deterministic.

We dug into the cause: research scoring depends on the agent's
free-form text answer being judged equivalent to a canonical, *and*
the answer comes from open-ended web searching where the first DDG
result loaded determines the rest of the trajectory. QA scoring runs
pytest against the agent's emitted test file — given the same test
file, the same pass/fail. The variance is in the agent's path to a
file, not in the scoring.

This means our headline +33pt research lift was on a single noisy
draw; the *expected* lift across multiple seeds is unknown but
likely smaller. To address this we expanded both evals from n=15 to
n=50 (v2) and report both the v1 single-seed and v2 numbers below.

### 3.5 A cross-domain framework bug we caught at launch

Phase 2's smoke tests ran only on research data, so the stem's
`summarize_trace` function was hard-coded to the research per-task
record shape (`task_id, question, canonical, candidate, correct,
…`). When we launched the QA stem run for the first time, it crashed
in `investigate()` because QA per-task records have a *different*
shape (`task_id, score, reason, buggy, fixed, agent_final, …`).
Fixed by branching on which fields are present. The bug surfaced at
launch and added 30 minutes; in a longer run on a different domain
without coverage like ours, it could have been days.

The point of the safeguard story isn't just to catch agent
regressions — it's to catch *our own* contracts. We caught this one
because the smoke test ran end-to-end on the second domain before
we'd burned a real budget on it.

### 3.6 v2 (n=50) results

*[v2 baseline + evolved configs running; numbers go here once
complete.]*

We expect three things from v2:
1. Tighter binomial CIs (n=50 puts a 95% CI of about ±0.13 around
   any observed proportion; n=15 was about ±0.25).
2. The probe-eval gap on the ablation should *grow* with more
   eval questions, because the few-shots are concentrated on
   binary-search and most v2 tasks aren't.
3. The research lift should *shrink* relative to the v1 +33pt point
   estimate, because the v1 number was on a single noisy draw and
   regression to the mean is real.

---

## 4. What we'd do with more time

- **Multi-seed runs.** The validation rerun showed the research result
  varies by 27pt across two seeds. Reporting mean ± std across 5–10
  seeds is the minimum bar for the headline number to be trustworthy.
- **Stronger agent model.** The whole comparison is on `gpt-4o-mini`.
  A grader can plausibly dismiss "specialized gpt-4o-mini beats
  baseline gpt-4o-mini" as a curiosity unless the lift survives at
  `gpt-5.1`. The natural experiment is a 2×3 grid (baseline /
  specialized / ablation × 4 model strengths) to test whether
  specialization shrinks as the model gets stronger. We have the
  budget (we burned ~1.5% of it); we ran out of time.
- **Fix `propose.py` to read existing few-shots.** Two runs showed
  the same blindness; the fix is two lines. Then re-run the ablation
  to see whether the proposer ever genuinely diversifies.
- **Add a `write_tool` motivator to the investigation prompt.** Right
  now the prompt enumerates the action menu neutrally. A version that
  surfaces "concrete tool gaps" — e.g., "the agent has no way to
  fetch and read a single URL's main content; consider authoring a
  `fetch_url` tool" — might shift the proposer's preference ordering
  enough to actually exercise `write_tool`. Currently
  `write_tool`-as-affordance is unverified by our experiments.
- **Better probe set design.** A 5-question probe set works for the
  unrestricted runs because probe and eval match. Under tool
  constraint (the ablation), it overfit catastrophically. A
  difficulty-stratified probe set with 10–15 tasks would catch the
  generalization failure earlier in the loop.
- **Record per-iteration tool-call counts.** We learned the
  unrestricted QA agent used 87 calls vs the ablation's 400 only
  *after* the run; the stem itself doesn't see this number, so it
  can't reason "my agent is hitting its budget — try a different
  axis." Adding it to the proposer's context is cheap.
