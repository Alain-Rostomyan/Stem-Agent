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
| Phase 2 smoke validation (investigate+propose live calls) | $0.039 | $0.147 | one investigate + one propose call, both successful |

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

## Phase 2 — Stem evolution

### 2026-05-01: First investigate call on research domain — fetch_url proposed unprompted

Ran `stem.investigate.investigate(...)` on the research domain with the 5 probe
tasks and the 15 baseline traces from the locked baseline. Single gpt-5.1 call,
$0.02, 1685 output tokens. Saved analysis at
`runs/investigate_research_20260501T231951.md`.

**Two writeup-worthy observations:**

1. **The stem independently proposed a `fetch_url` tool.** From the analysis:

   > Tooling: lightweight HTML/text fetcher (if allowed): A `fetch_url` tool
   > that returns cleaned page text or HTML for a given URL. This avoids relying
   > solely on search snippets and lets the agent systematically parse
   > tables/lists.

   We deliberately left no URL fetcher in the starter set, hoping the stem
   would identify it as the missing capability. It did, on the first
   investigate call, with the right justification (snippets are insufficient
   for systematic table extraction). This is the cleanest possible "stem
   discovers what it needs" story.

2. **The stem proposed `run_python` as a counting tool, not a code tool.** This
   was unexpected. The analysis recommends:

   > Encourage `run_python` for counting: When there are more than ~5 items,
   > use run_python to store items as a list of dicts, filter and count. This
   > reduces off-by-one and omission errors.

   `run_python` was already in the starter set as a generic "execute Python"
   tool — but using it specifically as a way to *materialize a structured list
   and count programmatically* is a workflow-discipline affordance, not a tool
   gap. The stem identified this as a way to fix the E10 (SA Bantu = 8 vs 9)
   off-by-one failure mode without adding any tool.

   This is a richer specialization story than just "stem wrote new tools." The
   stem is also re-purposing existing tools by changing how the agent uses
   them via prompt scaffolding.

**Other things in the analysis worth keeping:** explicit stopping criterion
("once you have a complete list of items in the universe and have applied the
filter to each item, immediately compute the count and answer; do not perform
additional web searches"), shape-specific few-shot exemplars (the stem
leveraged the `shape` tags we added to the eval/probe sets), and ambiguity
handling instructions (cross-check definitions across sources).

**What this means for Phase 2 next steps.** The investigate phase output is
rich enough that propose.py has a real menu of changes to draw from — adding
a fetch_url custom tool, modifying the system prompt to enforce the structured
template, adding shape-specific few-shots. We didn't need to over-engineer
investigate to get useful proposals; the model did the heavy lifting from a
modest scaffold.

### 2026-05-02: Phase 2 complete — Phase 3 (real stem runs) is next

All six Phase 2 modules in: investigate, propose, test_proposal, commit,
stop_check, run_stem. CLI works: `python -m stem.run_stem --domain research`.
40+ offline assertions in phase2_smoke_test.py all green. Project spend
~$0.15 total against a €50–70 budget — comfortable headroom for Phase 3.

The first real stem run will be the moment of truth. Two things to watch
for:

1. **Whether the proposer's sequencing mistake (modify_prompt-before-tool)
   gets corrected by the loop.** That's the safeguard story working. If
   instead the loop accepts a no-op proposal and plateaus on a config that
   still can't reach web data, the rejection rule needs tightening (e.g.,
   require strict improvement, not just non-regression).

2. **Whether the stem actually writes a fetch_url tool.** It proposed it
   in investigate. The loop has to follow through. If iteration 2 picks
   write_tool and the resulting tool passes sanity, that's the strongest
   single artifact we can produce for the writeup.

Recommended starter run: `--max-iterations 10 --stem-budget-calls 25`
(half the defaults). Cost cap ~$0.50. If clean, scale to defaults.

### 2026-05-01: First propose call — sequencing mistake worth keeping

Ran `stem.propose.propose(...)` with the saved investigate analysis as input,
starting from `default_config(domain='research')` (which has only 4 of 7
starter tools enabled — no `web_search`, `write_file`, or `run_shell_command`).
Single gpt-5.1 call, $0.019. Saved at `runs/propose_research_20260501T235254.json`.

**The proposer chose `modify_prompt` and authored a 6.1k-character system
prompt instructing the agent to "use targeted web_search queries..." — but
`web_search` isn't in the current enabled_tools.** The proposer's own
rationale even notes: "the agent already has run_python but no web access."
And it still went for a prompt change before enabling the missing tool.

This is a sequencing mistake: the prompt depends on tools that haven't been
turned on yet. Applied as-is, this proposal would not move the probe score —
the agent still literally cannot reach web data.

**Why this is the right kind of failure to keep.** The whole point of the
test_proposal + stop_check + commit-or-rollback loop is to catch and correct
exactly this kind of plausible-but-incomplete first move. We expect to see
the next iteration:
  - probe score doesn't improve (no web access)
  - proposer sees the score plateau plus the unchanged tool inventory
  - next action becomes `enable_tool(web_search)`, then later `modify_prompt`
    can take effect

If we'd hand-tuned the proposer to always enable tools before tweaking the
prompt, we'd hide a story the rubric explicitly wants ("the path your thinking
took, especially where things didn't go as expected"). The imperfect proposer
+ correcting loop is the writeup, not a bug to engineer around.

What we lose by leaving it: one wasted evolution iteration's worth of LLM
calls — call it ~$0.05 — every time the proposer makes a sequencing mistake.
That's an acceptable cost given budget headroom (~$50+ remaining).

---

## Phase 3 — First Stem Run (complete, smoke run with 10-iter / 25-stem-call cap)

### 2026-05-02 12:07: Run finished, plateau-stopped at iter 5

**Headline.** Final probe score **0.600** on the 5-question probe set, up from
**0.267** baseline — a +33pt lift in 5 iterations. Cost: $0.171 total
($0.103 stem + $0.067 agent + $0.001 judge). Well under the $0.50 cap.
Stop reason: plateau (3 successive accepted iterations at probe=0.600).

**Evolution path (commits in master).**
- step 0: initial config (no probe)
- step 1: enable_tool(web_search) → 0.400 ACCEPT
- step 2: modify_prompt (micro-plan + scratchpad + stop-rule, 6.1k chars) → 0.600 ACCEPT
- step 3: add_few_shot (Muslim-majority of top-10 populous, count=4) → 0.600 ACCEPT (tie kept)
- step 4: add_few_shot → 0.400 REJECT (rolled back — no commit in log)
- step 5: add_few_shot (Muslim-majority of top-10 populous, count=3) → 0.600 ACCEPT

**Watchpoint #1 (sequencing): RESOLVED POSITIVELY.** Captured in the 12:01
entry above. tl;dr: the live run picked enable_tool(web_search) first,
fixing the smoke-test sequencing failure.

**Watchpoint #2 (fetch_url custom tool): RESOLVED NEGATIVELY.** The stem
never wrote fetch_url, even though the investigation analysis explicitly
suggested it as an "Optional custom helper tool." The loop preferred
prompt-tweaks and few-shots throughout. This is fine — fetch_url was
flagged optional, and the score still climbed to 0.600 without it. But
it does mean the writeup can't claim "the stem authored a custom tool";
that capability is in the harness but unexercised on this run.

**Surprise: the proposer added two near-duplicate few-shots about the same
question, with contradicting answers.** Step 3's example classifies
Nigeria as Muslim-majority (count=4); step 5's example classifies Nigeria
as religiously mixed (count=3). Same question, different worked answer,
both now in `configs/research.json`. The proposer doesn't appear to track
what's already in `few_shot_examples` when authoring a new one — the
second example reads as if written from scratch with no awareness of the
first. Worth noting in the writeup as a real proposer limitation. Also
worth checking whether this hurt the agent: the few-shots-count went from
0 to 1 to 2 with probe stuck at 0.600 either way, so the contradiction
doesn't seem to have actively poisoned the score, but it's not earning
its keep either.

**Step 4 rejection (rollback story works).** The third add_few_shot
proposal dropped probe to 0.400 and got rolled back to step 3's HEAD via
`git reset --hard`. No step-4 commit appears in the log. This is the
exact scenario the commit/rollback module was built for, and it caught a
regression on its first real opportunity.

**Plateau detection: also works.** Three successive iterations at probe=
0.600 (steps 2, 3, 5) triggered the stop_check before max_iterations=10.
This is the right call — continuing to spend stem calls on a flat curve
is wasteful. But it also means we don't know whether the stem WOULD have
proposed fetch_url given more rope; the plateau-stop foreclosed that.

**Tool-budget headroom.** Stem made 6 calls of 25 budgeted (24%). Agent
made 135 of 400 (34%). Judge 25 of 200 (13%). Plenty of room to run a
real 50-call evolution if we want it, but the smoke run already gives a
clean phase-3 result and a writeup-worthy story.

**Probe vs. eval gap.** The 0.600 number is on the 5-question probe set
the stem trained against. The locked baseline of 0.267 was on the
15-question eval set. These are not directly comparable; we should
re-run the evolved config on the full eval set before claiming a
generalization win. That's the next concrete action.

**Decision pending.** Two options for next step:
  1. Run the evolved config (`configs/research.json` at HEAD) on the full
     15-question eval set to get a baseline-comparable score. ~$0.10.
  2. Run a second, longer stem evolution (defaults: 20 iter, 50 stem
     calls) and see whether it goes farther — especially whether plateau
     is hit again, or whether it eventually reaches for fetch_url /
     planner_executor architecture / etc. ~$0.60–1.30.

Option 1 is the discipline move (verify probe→eval generalization before
spending more on evolution). Option 2 is the explore move. Both are
cheap; can do option 1 first and option 2 if option 1 looks solid.
