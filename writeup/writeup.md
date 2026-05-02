# Stem Agent Writeup

*A meta-agent that turns a generic agent into a domain-specialized one,
tested on research, Python QA, and a tool-starved ablation.*

---

## 1. Approach

The project has two agents. The **stem** is short-lived and
domain-agnostic: given a problem class, probe tasks, and baseline traces,
it investigates the domain and evolves a config. The **specialized
agent** is the frozen output: a JSON artifact with a system prompt,
enabled tools, few-shots, optional custom tools, architecture, and domain
metadata.

The stem follows a simple loop:

`investigate -> propose one change -> test on probes -> accept/rollback -> stop`

Each proposal is one of five actions: modify the prompt, enable a
starter tool, write a custom tool, add a few-shot example, or switch
architecture. Every accepted change is committed to git; failed changes
are rolled back. Stopping is either score plateau or budget exhaustion.
This makes the evolution auditable rather than an opaque prompt-editing
session.

I chose a "middle route" on tools. The stem starts with seven generic
tools (`read_file`, `write_file`, `list_directory`, `run_python`,
`run_shell_command`, `web_search`, `call_llm`) but can choose subsets or
author new tools. The goal was not to hand-build a research or QA agent,
but to see whether the same stem could grow different specializations.

Evaluation covered two domains plus one ablation:

- **Research:** open-domain counting/synthesis questions over a finite
  universe, e.g. "Of the 27 EU members, how many use the Euro?" Scoring
  uses an LLM judge against canonical answers plus citation URL checks.
- **QA:** given a buggy Python function and a spec, write a pytest file
  that fails on the buggy implementation and passes on a hidden fixed
  implementation.
- **B-style ablation:** QA again, but the stem starts with only
  `run_python` and `call_llm`. This was intended to test whether the
  stem would bootstrap missing tool capability.

All agent/judge evals used `gpt-4o-mini`; the stem used `gpt-5.1`.
Per-task max steps were capped at 12 for baseline and evolved agents.
The local cost summary for all runs quoted here is **$4.71**; the
OpenAI dashboard is authoritative, but the API key was not on my
account.

---

## 2. Results

### 2.1 Main results

The first eval set had 15 tasks. Because those results looked too clean,
I expanded both domains to 50 tasks and later ran four additional
sequential research v2 pairs to estimate variance.

| Run | v1 (n=15) | v2 single run (n=50) | v1 delta | v2 delta |
|---|---:|---:|---:|---:|
| Research baseline | 0.267 (4/15) | 0.300 (15/50) | anchor | anchor |
| Research evolved | 0.600 (9/15) | 0.380 (19/50) | +33.3 pt | +8.0 pt |
| QA baseline | 0.667 (10/15) | 0.800 (40/50) | anchor | anchor |
| QA evolved | 0.800 (12/15) | 0.780 (39/50) | +13.3 pt | -2.0 pt |
| QA ablation | 0.267 (4/15) | 0.300 (15/50) | -40.0 pt vs QA | -50.0 pt vs QA |

The v1 research evolved score was not stable: the same config later
scored 0.333 on the same 15-task eval. The v2 single run shrank the
headline research lift from +33 pt to +8 pt. Four clean sequential v2
pairs shrank it further:

| Condition | Mean | Std | Range |
|---|---:|---:|---:|
| Research baseline v2 | 0.370 | 0.030 | 0.340-0.420 |
| Research evolved v2 | 0.385 | 0.033 | 0.360-0.440 |

So the most honest research conclusion is **near parity with a small,
noisy edge for the evolved config**, not a robust win. This is still
structurally interesting because the research config used fewer tools
than the generic baseline: 5 tools instead of all 7.

The QA conclusion is harsher: specialization helped on v1, but did not
generalize to the broader set. On v2, the evolved QA agent scored
slightly worse than the generic baseline. The ablation result was robust:
restricting the toolset caused a large collapse.

### 2.2 Research shape breakdown

The research set has three shapes: simple aggregation (`agg`), filtering
a universe by an attribute (`filter`), and recent/time-sensitive lists
(`granular`).

| Shape | v1 baseline | v1 evolved | v2 baseline | v2 evolved |
|---|---:|---:|---:|---:|
| agg | 4/5 | 4/5 | 12/17 | 10/17 |
| filter | 0/5 | 3/5 | 3/17 | 9/17 |
| granular | 0/5 | 2/5 | 0/16 | 0/16 |

The v1 story looked ideal: the stem seemed to unlock both `filter` and
`granular`. The larger eval rewrote that. The `filter` improvement was
real on the first v2 run; `agg` got worse; `granular` was not solved at
all. Later sequential runs got nonzero granular scores, but the overall
baseline/evolved comparison remained near parity.

### 2.3 What the stem actually changed

The same stem produced different configs for the two domains:

| Field | Research | QA |
|---|---|---|
| Enabled tools | read, list, py, llm, `web_search` | read, list, py, llm, `write_file`, `run_shell_command` |
| Prompt theme | list-then-filter-then-count | infer spec, do not mirror buggy output |
| Few-shots | 2 research examples | 1 binary-search test example |
| Architecture | single loop | single loop |
| Custom tools | 0 | 0 |

Two useful specializations emerged:

- Research learned an explicit "enumerate the universe, build a table,
  then count" workflow, plus cross-checking for subtle demographic or
  linguistic attributes.
- QA learned a key testing failure mode: never derive expected outputs by
  calling the buggy implementation.

However, the stem authored **zero custom tools** across all runs,
including the ablation that was designed to make tool authoring useful.
That is a finding, not just a missing feature.

---

## 3. What Surprised Me

### 3.1 The proposer avoided writing tools

Across 19 proposals (5 research, 4 QA, 10 ablation), `write_tool` was
chosen zero times. The observed preference order was roughly:

`enable_tool -> modify_prompt -> add_few_shot -> switch_architecture -> write_tool`

Even when `enable_tool` was exhausted in the ablation, the proposer kept
adding few-shots instead of writing a helper. My best explanation is
risk asymmetry: a prompt or example is easy to emit and parse; a useful
tool has to import cleanly, handle edge cases, and actually help inside
the harness. The investigation prompt also did not force the stem to
name concrete missing affordances, so `write_tool` remained available in
the menu but unattractive in practice.

### 3.2 The proposer duplicated few-shots

The proposer did not see the full body of existing few-shots when
authoring a new one. That caused:

- two research examples about the same "top 10 populous countries /
  Muslim-majority" question, with conflicting answers;
- five ablation examples about binary search and no coverage of other
  QA shapes.

This is a straightforward design bug: `propose.py` should include
existing few-shot bodies, not only the count.

### 3.3 The ablation passed probes and failed eval

The ablation matched the unrestricted QA probe score (0.800) but scored
0.267 on v1 eval and 0.300 on v2 eval. Most failures were
`no_test_file`: with only `run_python` and `call_llm`, writing a pytest
file requires composing and executing Python file I/O. The
planner/executor architecture also spends one step planning before it can
act, so the agent often ran out of its 12-step budget before creating
the artifact.

This is the clearest tool lesson: taking away `write_file` does not just
make the agent less convenient; it changes the effective task difficulty.
It also shows that five probe tasks are too few under tool constraints.

### 3.4 Research variance changed the conclusion

Research had large run-to-run variance because the agent's trajectory
depends on web search results, citation behavior, and judge decisions.
The same v1 research config moved from 0.600 to 0.333 on rerun. A
parallel multi-seed attempt was invalid because web search starved
(`urls=0` on most tasks), so I reran sequentially. The four clean v2
pairs produced only a +1.5 pt mean lift.

This is the main methodological lesson: **15 tasks were too few, and one
research seed was not enough even at n=50.** The original story was
"the stem improved both domains"; the final story is more honest:
specialization changed agent behavior, but reliable generalization was
weak except for the ablation failure, which was very stable.

### 3.5 A framework bug surfaced at domain transfer

The first QA stem run crashed because `summarize_trace` assumed the
research per-task schema (`question`, `canonical`, `candidate`,
`correct`). QA records have different fields (`score`, `reason`,
`buggy`, `fixed`, `agent_final`). The fix was simple branching, but the
bug is important: a stem-agent framework needs domain-boundary tests, not
only tests on the first domain that happens to work.

---

## 4. More Time

- Run 10+ sequential research seeds and a stronger-model condition
  (`gpt-5.1-mini` or `gpt-5.1`) before claiming a stable research edge.
- Fix the proposer context so it sees existing few-shots and is asked to
  identify concrete missing tool affordances.
- Improve probe design: 10-15 stratified probes would likely catch the
  ablation's probe/eval gap earlier.
- Feed per-task tool-call counts back into the stem. The ablation burned
  400 calls while unrestricted QA used 87; the proposer never saw that
  signal.

---

## Bottom Line

The stem did produce different specialized agents for different domains,
and the git/probe loop made the evolution auditable. But the stronger
result is the negative one: the original small eval overstated the
benefit. On broader and repeated evals, QA specialization did not beat
baseline and research specialization was near parity. The most robust
finding is that the tool-starved ablation collapsed, showing that the
right basic tools matter more than many few-shots or prompt edits.
