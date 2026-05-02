# Stem Agent

A meta-agent that, given a problem class (Research, QA), investigates the domain,
proposes specialization changes one at a time, gates them with a probe-set
score-and-sanity check, and rolls back failures via `git reset --hard`. The output
is a JSON config that instantiates a domain-specialized agent.

**See `writeup/writeup.md` for the 4-page result writeup.** This README is just
setup + reproduction.

## Setup

```bash
python -m venv .venv
. .venv/Scripts/activate              # bash on Windows / .venv\Scripts\activate.ps1 on PowerShell
pip install -r requirements.txt
cp .env.example .env                  # put your real OPENAI_API_KEY in .env
```

The `.env` is loaded automatically by `stem/dotenv.py`; a real shell `export
OPENAI_API_KEY=...` still wins if you'd rather not edit a file.

## Quickstart â€” reproduce the writeup numbers

The repo ships with both probe and eval task sets locked, plus the three evolved
configs (`configs/research.json`, `configs/qa.json`, `configs/qa_ablation.json`).
To reproduce the headline numbers without running a stem evolution:

```bash
# v1 evals (n=15, single-seed, what's quoted in writeup Â§2.1)
python -m scripts.run_baseline --domain research --split eval                                   # baseline 0.267
python -m scripts.run_baseline --domain qa --split eval                                         # baseline 0.667
python -m scripts.run_baseline --domain research --split eval --config-path configs/research.json     # 0.600
python -m scripts.run_baseline --domain qa --split eval --config-path configs/qa.json                 # 0.800
python -m scripts.run_baseline --domain qa --split eval --config-path configs/qa_ablation.json        # 0.267

# v2 evals (n=50, larger set built by scripts/build_eval_v2.py)
python -m scripts.run_baseline --domain research --split eval_v2                                # baseline v2
python -m scripts.run_baseline --domain qa --split eval_v2                                      # baseline v2
# ...and the same --config-path forms above with --split eval_v2

# Sequential research variance check used in the writeup
python -m scripts.sequential_research --pairs 4 --model gpt-4o-mini --sleep-s 30
```

**Cost.** Each `gpt-4o-mini` 15-task eval costs ~$0.05. The 50-task v2 evals run
about 3Ã— that. The ablation costs ~10Ã— as much because the constrained agent
burns its full call budget on each task. Expect $0.10â€“$0.50 to reproduce the v1
numbers, $0.50â€“$2 for v2, and about $1.4 for the 4-pair sequential research
variance check.

**Determinism.** QA is deterministic (pytest scoring is fixed given the test code).
Research is *not* â€” the agent's first DDG result determines the trajectory, and the
score swings by 20â€“30 points across reruns. The writeup Â§3.4 documents this.

## Quickstart â€” run the stem from scratch

```bash
# Standard runs (defaults: 20 iterations, 50 stem calls)
python -m stem.run_stem --domain research
python -m stem.run_stem --domain qa

# Tighter smoke run
python -m stem.run_stem --domain research --max-iterations 10 --stem-budget-calls 25

# B-style ablation: only run_python and call_llm enabled
python -m stem.run_stem --domain qa \
    --starter-tools run_python,call_llm \
    --run-name qa_ablation
```

Each run requires a clean working tree (`_ensure_clean_tree` refuses dirty trees,
including untracked files). Each accepted iteration creates a real commit on
`master`. Rejected iterations run `git reset --hard HEAD` and try again.

After a stem run finishes, `configs/<run_name>.json` is the artifact and
`runs/stem_<run_name>_<timestamp>.json` is the full evolution log
(investigate prompt, every proposal, every probe outcome, every commit-or-rollback
decision).

## Project layout

```
stem-agent/
â”œâ”€â”€ stem/                 the stem itself
â”‚   â”œâ”€â”€ llm_client.py     OpenAI wrapper, budget enforcement, jsonl logging
â”‚   â”œâ”€â”€ investigate.py    one-shot domain analysis (gpt-5.1)
â”‚   â”œâ”€â”€ propose.py        one-shot proposal (gpt-5.1, JSON-mode)
â”‚   â”œâ”€â”€ test_proposal.py  probe-set test + sanity-task gate
â”‚   â”œâ”€â”€ commit.py         git accept / git reset --hard reject
â”‚   â”œâ”€â”€ stop_check.py     plateau (Î”<0.05 over 3 accepts) or budget
â”‚   â””â”€â”€ run_stem.py       main loop + CLI entry point
â”œâ”€â”€ agent/
â”‚   â”œâ”€â”€ config.py         AgentConfig schema, load_config, save_config
â”‚   â”œâ”€â”€ runner.py         instantiates a specialized agent (single_loop or planner_executor)
â”‚   â””â”€â”€ baseline.py       generic baseline agent (all 7 starter tools, generic prompt)
â”œâ”€â”€ tools/
â”‚   â”œâ”€â”€ starter/          the 7 starter tools (locked at Phase 0)
â”‚   â””â”€â”€ registry.py       tool registration + LLM-readable descriptions
â”œâ”€â”€ evals/
â”‚   â”œâ”€â”€ qa/probe_set.json, eval_set.json (n=15), eval_v2_set.json (n=50), runner.py
â”‚   â””â”€â”€ research/         same, with LLM-judge scoring + URL-citation validity
â”œâ”€â”€ scripts/
â”‚   â”œâ”€â”€ run_baseline.py   eval runner: --domain, --split, --config-path
â”‚   â”œâ”€â”€ build_eval_v2.py  generates eval_v2_set.json (used once; checked into the tree)
â”‚   â””â”€â”€ cost_summary.py   roll up local LLM spend across all runs/*.jsonl
â”œâ”€â”€ configs/              the three evolved configs that ship with the repo
â”œâ”€â”€ runs/                 per-run logs (gitignored; recreated by reproduction)
â”œâ”€â”€ writeup/
â”‚   â””â”€â”€ writeup.md        final deliverable
â””â”€â”€ stem_agent_briefing.md  the original assignment + design decisions
```

## Useful operational commands

```bash
# Roll up local spend across every run
python -m scripts.cost_summary

# Build the v2 eval files (already checked in; re-run only if you edit the script)
python -m scripts.build_eval_v2

# Run an arbitrary saved config against an eval (the writeup figures all use this path)
python -m scripts.run_baseline --domain qa --split eval_v2 --config-path configs/qa_ablation.json
```

