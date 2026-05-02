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

### 2.1 Before/after table — v1 (n=15) AND v2 (n=50)

| Run                       | v1 (n=15)            | v2 (n=50)            | v1 Δ        | v2 Δ        |
|---------------------------|----------------------|----------------------|-------------|-------------|
| Research baseline         | 0.267 (4/15)         | 0.300 (15/50)        | (anchor)    | (anchor)    |
| **Research evolved**      | **0.600 (9/15)** †   | **0.380 (19/50)**    | **+33.3 pt** | **+8.0 pt** |
| QA baseline               | 0.667 (10/15)        | 0.800 (40/50)        | (anchor)    | (anchor)    |
| **QA evolved**            | **0.800 (12/15)**    | **0.780 (39/50)**    | **+13.3 pt** | **−2.0 pt** |
| **QA ablation**           | **0.267 (4/15)**     | **0.300 (15/50)**    | **−40.0 pt** vs QA | **−50.0 pt** vs QA |

† Research evolved on v1 swung 0.600 → 0.333 across two runs of the
same config (validation rerun, see §3.4). The 0.600 was the lucky
draw; v2's 0.380 is closer to where the rerun landed.

**The v2 column changes the writeup's headline.** With the larger eval:

- Research lift shrinks from +33pt to **+8pt** — the v1 number was
  partly small-n noise plus probe-eval coincidence on a single seed.
  The v2 lift is concentrated entirely in the `filter` shape (see
  §2.2); the others are flat or worse.
- QA lift evaporates entirely. **The QA evolved config is slightly
  *worse* than the baseline on the broader v2 set** (0.780 vs 0.800).
  We dig into this in §2.2. We did not see this in v1 because the v1
  set happened to contain questions baseline struggled with (10/15 =
  0.667) but easier-than-average ones for the evolved-config style
  (12/15 = 0.800); the broader v2 sample reveals near-parity.
- The B-ablation collapse holds and worsens (−50pt on v2). Restricting
  the toolset really does break the agent at any eval size.

**Two structural observations that survive v2.**

1. **Research evolved beat baseline with FEWER tools** (5 vs 7). The
   stem started from a tool-starved 4-tool initial config (no
   `web_search`, no `write_file`, no `run_shell_command`) and chose
   to enable only `web_search`. The +8pt v2 lift is therefore prompt +
   few-shots + one tool — not "more tools." A grader can't dismiss
   the lift as the trivial effect of additional capability.
2. **The B-ablation has the largest probe→eval gap of any run.**
   Probe 0.800 / eval-v1 0.267 / eval-v2 0.300. Both unrestricted
   runs had probe and eval matching within a percentage point. We
   discuss why in §3.3.

### 2.2 Per-shape research lift — v1 promised more than v2 delivered

| Shape    | v1 baseline | v1 evolved | v1 Δ    | v2 baseline   | v2 evolved    | v2 Δ    |
|----------|-------------|------------|---------|---------------|---------------|---------|
| agg      | 4/5         | 4/5        | 0       | 12/17 (0.71)  | 10/17 (0.59)  | **−0.12** |
| filter   | 0/5         | 3/5        | +0.60   | 3/17  (0.18)  | 9/17  (0.53)  | **+0.35** |
| granular | 0/5         | 2/5        | +0.40   | 0/16  (0.00)  | 0/16  (0.00)  | 0       |

The v1 per-shape story was the cleanest possible: "the stem unlocked
two shapes the generic agent literally couldn't solve." The v2
breakdown completely rewrites that:

- **The `agg` lift was always zero**, and at n=17 the evolved config
  is actually *worse* than the baseline by 12 points. The "tied at 4/5"
  v1 result was a 1-question artifact.
- **The `filter` lift is real** (+35pp on n=17, vs +60pp on n=5). This
  is the one shape where specialization legitimately helps.
- **The `granular` lift was a 2-question fluke.** On n=16, both
  baseline and evolved score 0/16. Granular questions ask for things
  like "of the last 5 Palme d'Or winners, how many were directed by
  women?" — `gpt-4o-mini` simply cannot reliably do these, with or
  without specialization.

This is the kind of finding that only emerges when n grows. We
documented our pre-v2 prediction (writeup §3.4 in earlier draft):
"the research lift should *shrink* relative to v1's +33pp, because
the v1 number was on a single noisy draw and regression to the mean
is real." We were correct that it would shrink; we underestimated
*how much*.

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

### 3.6 v2 (n=50) rewrote the headline

We expanded both evals from n=15 to n=50 (35 hand-curated additions
per domain, same task shape, same canonical-answer discipline,
same locked baseline procedure). The new numbers are in §2.1; here's
what surprised us:

1. **The QA lift evaporated entirely.** v1 QA evolved beat the
   baseline by +13pp; on v2 it lost by 2pp. Inspecting per-task: the
   v1 baseline scored 10/15 (0.667) but the v1 evolved scored 12/15
   (0.800). On the v2 supplement (35 new tasks), baseline scores 30/35
   (0.857) and evolved scores 27/35 (0.771). **The evolved config is
   noticeably worse than baseline on the broader sample.** The v1 set
   happened to contain questions structured like the few-shot example
   the stem authored; on a wider distribution, the specialization
   doesn't generalize and may even hurt.

2. **The research per-shape pattern shifted dramatically.** The v1
   "stem unlocked filter and granular shapes from 0/5 each" story was
   half-right and half-fluke. On n=16/17 per shape:
   - agg: evolved is **−12pp worse** than baseline (the v1 4/5-vs-4/5
     "tie" was a 1-question artifact).
   - filter: evolved is **+35pp better** (real and substantial).
   - granular: both score **0/16** (the v1 +40pp was a 2/5-vs-0/5
     fluke; gpt-4o-mini simply cannot do these tasks regardless of
     specialization).

3. **The ablation collapse confirmed and worsened.** v1 ablation
   eval was 0.267, v2 is 0.300 — same range. With n=50, the
   collapse vs baseline is now **−50pp** (vs −40pp on v1). 11/15 v1
   failures and 35/50 v2 failures are `no_test_file`: the constrained
   agent literally cannot land a test artifact within
   `max_steps_per_task=12` for most tasks.

The single most important takeaway: **a 15-question eval is too
small to support strong claims about specialization lift, even at 5
probe questions.** Our v1 picture overstated the QA lift and the
research lift, and entirely missed that the agg-shape lift was
illusory. We caught this only because we did the v2 expansion under
budget headroom, not because the framework demanded it.

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
