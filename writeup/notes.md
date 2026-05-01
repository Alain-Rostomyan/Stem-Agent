# Writeup Notes — Working Document

A scratchpad for observations, surprises, and "remember this for the writeup" moments
captured *as they happen*. Not the writeup itself. The graders care about the path of
thinking — especially where things didn't go as expected — so we want raw notes from
each phase, not retrospective rationalizations written at the end.

When something interesting happens (rollback, surprising result, dead end, design
pivot), add an entry here with the date. The final 4-page writeup will be drafted
from this file.

---

## Phase 1 — Eval Baselines

### 2026-04-30 → 2026-05-01: The research domain almost broke the project

**What happened.** Initial research eval set was short-answer factoid QA (multi-hop
geography, common misconceptions, etc.). Baseline (gpt-4o-mini + DDG snippets) hit
**100% on 15/15** twice in a row. Even after rewriting questions to be deliberately
harder (multi-hop, misconception traps), baseline still saturated.

**Why.** Snippet-skim plus a small LLM is sufficient for any question whose answer
appears as a contiguous string anywhere on the indexed web — which includes most
"multi-hop" questions, because Google has already indexed pages where the joined
answer is one sentence. "Multi-hop difficulty" was the wrong frame; "is the answer
pre-written somewhere" was the right frame.

**The pivot.** Switched the research domain from short-answer factoid QA to
**synthesis questions** structured around three shapes that defeat snippet-skim:
- **agg**: numeric aggregation (count/sum) over per-item lookups.
- **filter**: filter axis ≠ list axis (canonical list, non-canonical filter).
- **granular**: at least one ingredient too granular/recent for parametric memory.

**The dry run that confirmed it.** Wrote 9 trial questions (3/shape) into
`evals/research/dry_run_set.json`, ran baseline against them. Result: **22.2% (2/9)**.
Cost: $0.034. Per-shape: agg 0/3, filter 1/3, granular 1/3. None saturated, none
floored — the shape works.

### Three distinct failure modes in the dry run

This is one of the strongest exhibits we have. Every failure was *one of these three*,
and they're all things a specialized agent could close — none are "model too small"
or "more steps would have helped":

1. **Search loop** (e.g. NATO populations <5M, task `rs_dry_03`): baseline issued the
   *same* `web_search` query 12 times in a row and gave up. Tool was cached, returns
   were identical, no exploration. → Specialization opportunity: decomposition discipline
   ("first list the 32 members, then look up each").

2. **Tool-misuse loop** (e.g. 2024 films runtime `rs_dry_01`, Nobel laureates
   `rs_dry_09`): baseline tried `read_file` on URLs (Wikipedia, Box Office Mojo,
   Britannica…). `read_file` is a local-file tool; every call failed silently. Baseline
   never adapted. → Specialization opportunity: write a `fetch_url` custom tool, OR learn
   to use `web_search` snippets more carefully, OR use `run_python` to fetch URLs.

3. **Quick-answer-wrong** (e.g. EU non-Latin scripts `rs_dry_04`): baseline terminated
   in 3 steps with answer "2", canonical answer "3" (probably missed Cyprus). → Specialization
   opportunity: verify by enumerating before answering.

**Why this is good for the writeup.** The standard "before/after" framing is "baseline
got it wrong, specialized got it right." We have something stronger: **three named
failure modes**, each with a corresponding specialization affordance. That's the
"path of thinking" the rubric asks for.

### Decision: do NOT modify the starter tool list

The starter tools (read_file, write_file, list_directory, run_shell_command,
run_python, web_search, call_llm) deliberately have no `fetch_url` or HTML scraper.
The dry run revealed this is the gap baseline can't cross. The briefing's Phase 2
thesis is that the *stem* discovers and bootstraps the missing capability; pre-adding
the tool would erase that opportunity.

This means: **the absence of a URL fetcher in the starter set is load-bearing for the
specialization story.** Worth saying explicitly in the writeup so it doesn't read as
an oversight.

### QA baseline (locked 2026-04-30)

QA: **66.7% (10/15)**. Failures split as 4 "tests too strict" / 1 "tests too weak".
This is the real before-number for QA. Less dramatic than the research framing but
the headroom is genuine.

### Research baseline locked (2026-05-01, post-pivot)

Research: **26.7% (4/15)** on the new synthesis-question eval set.

Per-shape breakdown:

| Shape | Score | Notes |
|---|---|---|
| agg | 4/5 (80%) | Solved when both list and filter live in canonical pre-tabulated form (UN langs, oceans, 7 Wonders, NFL teams). Failed only on E1 (NATO post-2000 accession dates). |
| filter | 0/5 (0%) | Total wipeout. |
| granular | 0/5 (0%) | Total wipeout. |

**Failure pattern is consistent across the dry run and the locked eval:**

- 9 of 11 failures hit `max_steps` (12) without producing any final answer at all.
  The agent looped — repeating the same `web_search` query, or trying `read_file`
  on URLs because no `fetch_url` tool exists in the starter set.
- 2 of 11 failures terminated quickly with the wrong answer (E9 NATO monarchies
  said 3 instead of 7 — incomplete enumeration; E10 SA Bantu said 8 instead of 9
  — off-by-one count).

So 82% of all failures are "stuck searching, no answer produced" — a workflow
discipline gap, not a knowledge gap. The specialized agent doesn't need to be
smarter at facts; it needs to (a) decompose list-then-lookup-per-item, (b) author
or substitute a URL-fetch capability, and (c) commit to an answer instead of
re-searching when partial data is enough.

The agg shape passing 4/5 is itself a useful finding: when the canonical list is
small and the filter is pre-tabulated, snippet-skim is sufficient even for
synthesis questions. The specialization gap is concentrated in shapes that
require *per-item* lookups baseline can't reach.

---

## Costs / Budget tracking

Budget: €50–70 total.

| Date | Spend | Cumulative | Notes |
|---|---|---|---|
| Phase 1 baselines | $0.02 | $0.02 | QA + 2× research baselines |
| Dry run (9 synth Qs) | $0.034 | $0.054 | High vs prediction; baseline iterated to step ceiling on most |
| Locked research baseline (15 synth Qs) | $0.054 | $0.108 | 26.7% accuracy, full eval run |

Local rollup: `python -m scripts.cost_summary`. Authoritative: platform.openai.com/usage.

---

## Open questions / things to revisit

- **Step ceiling fairness.** max_steps=12 per task is the same for baseline and
  specialized. Confirmed correct — the dry run failures were tool-misuse and search
  loops, not step starvation. If specialized agent ever wants more steps to "do the
  decomposition properly," that's a fairness violation; flag it in the writeup.

- **Probe set noise.** Probe set is 5 tasks. With synthesis questions getting 0/3 or
  1/3 within a shape during the dry run, 5 may be too small to detect plateaus
  reliably during stem evolution. Consider growing to 7–8 if early stem runs look
  noisy.

- **Question quality.** Dry-run answers were spot-checked but a few are best-guess
  (e.g. WC2022 zero-wins). Before scaling to 20-question eval, verify each canonical
  answer against a primary source.

- **Reproducibility risk on date-sensitive questions.** Several questions are pinned
  to "as of 2024" or "January 2025" — if we re-run on grading day in 2026, the
  underlying data may have shifted (NATO populations, building completions). Worth a
  one-line caveat in the eval set's metadata.

---

## Quotes / phrasings worth keeping for the writeup

- "Multi-hop difficulty was the wrong frame; *is the answer pre-written somewhere*
  was the right frame."
- "The absence of a URL fetcher in the starter set is load-bearing for the
  specialization story."
- "Three distinct failure modes, each with a corresponding specialization affordance."

---

## Phase 2 — Stem evolution (NOT YET STARTED)

Reserved for observations during stem runs. What proposals did it generate? What
got rolled back? What custom tools did it write? Was anything surprising?
