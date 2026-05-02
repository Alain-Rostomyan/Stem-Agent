# Stem Agent

A meta-agent that, given a problem class (Research, QA), investigates the
domain, proposes specialisation changes one at a time, gates them with a
probe-set score and sanity check, and rolls back failures via `git reset
--hard`. The output is a JSON config that instantiates a domain-specialised
agent.

See `writeup/writeup.md` for the final writeup. This README is setup and
reproduction only.

## Setup

```bash
python -m venv .venv
. .venv/Scripts/activate              # bash on Windows / .venv\Scripts\activate.ps1 on PowerShell
pip install -r requirements.txt
cp .env.example .env                  # put your real OPENAI_API_KEY in .env
```

The `.env` is loaded automatically by `stem/dotenv.py`; a real shell
`export OPENAI_API_KEY=...` still wins if you would rather not edit a file.

## Quickstart - reproduce the writeup numbers

The repo ships with both probe and eval task sets locked, plus the three
evolved configs (`configs/research.json`, `configs/qa.json`,
`configs/qa_ablation.json`). To reproduce the headline numbers without
running a stem evolution:

```bash
# v1 evals (n=15, single-seed, quoted in writeup section 2.1)
python -m scripts.run_baseline --domain research --split eval
python -m scripts.run_baseline --domain qa --split eval
python -m scripts.run_baseline --domain research --split eval --config-path configs/research.json
python -m scripts.run_baseline --domain qa --split eval --config-path configs/qa.json
python -m scripts.run_baseline --domain qa --split eval --config-path configs/qa_ablation.json

# v2 evals (n=50, larger set built by scripts/build_eval_v2.py)
python -m scripts.run_baseline --domain research --split eval_v2
python -m scripts.run_baseline --domain qa --split eval_v2
# ...and the same --config-path forms above with --split eval_v2

# Sequential research variance check used in the writeup
python -m scripts.sequential_research --pairs 4 --model gpt-4o-mini --sleep-s 30
```

**Cost.** Each `gpt-4o-mini` 15-task eval costs about $0.05. The 50-task
v2 evals run about 3x that. The ablation costs about 10x as much because
the constrained agent burns its full call budget on each task. Expect
$0.10-$0.50 to reproduce the v1 numbers, $0.50-$2 for v2, and about
$1.4 for the 4-pair sequential research variance check.

**Determinism.** QA is deterministic because pytest scoring is fixed
given the test code. Research is not: the agent's first DDG result
determines the trajectory, and the score swings by 20-30 points across
reruns. The writeup section 3.4 documents this.

## Quickstart - run the stem from scratch

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

Each run requires a clean working tree (`_ensure_clean_tree` refuses dirty
trees, including untracked files). Each accepted iteration creates a real
commit on `master`. Rejected iterations run `git reset --hard HEAD` and
try again.

After a stem run finishes, `configs/<run_name>.json` is the artefact and
`runs/stem_<run_name>_<timestamp>.json` is the full evolution log.

## Project layout

```text
stem-agent/
|-- stem/                 the stem itself
|   |-- llm_client.py     OpenAI wrapper, budget enforcement, jsonl logging
|   |-- investigate.py    one-shot domain analysis (gpt-5.1)
|   |-- propose.py        one-shot proposal (gpt-5.1, JSON-mode)
|   |-- test_proposal.py  probe-set test + sanity-task gate
|   |-- commit.py         git accept / git reset --hard reject
|   |-- stop_check.py     plateau (delta < 0.05 over 3 accepts) or budget
|   `-- run_stem.py       main loop + CLI entry point
|-- agent/
|   |-- config.py         AgentConfig schema, load_config, save_config
|   |-- runner.py         instantiates a specialised agent
|   `-- baseline.py       generic baseline agent
|-- tools/
|   |-- starter/          the 7 starter tools
|   `-- registry.py       tool registration + LLM-readable descriptions
|-- evals/
|   |-- qa/               probe/eval/eval_v2 sets + runner
|   `-- research/         probe/eval/eval_v2 sets + LLM-judge runner
|-- scripts/
|   |-- run_baseline.py   eval runner
|   |-- build_eval_v2.py  generates eval_v2_set.json
|   |-- sequential_research.py
|   `-- cost_summary.py
|-- configs/              evolved configs
|-- runs/                 per-run logs (gitignored)
|-- writeup/
|   `-- writeup.md        final deliverable
`-- stem_agent_briefing.md
```

## Useful operational commands

```bash
# Roll up local spend across every run
python -m scripts.cost_summary

# Build the v2 eval files (already checked in; re-run only if you edit the script)
python -m scripts.build_eval_v2

# Run an arbitrary saved config against an eval
python -m scripts.run_baseline --domain qa --split eval_v2 --config-path configs/qa_ablation.json
```
