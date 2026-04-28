# Stem Agent

A minimal stem agent that, given a problem class (e.g. QA, Deep Research), investigates the
domain, decides what kind of specialized agent it needs to become, and grows into one — with
git-backed safeguards so failed evolution steps roll back instead of bricking the agent.

This repo currently has Phase 0 (setup) wired up. See `stem_agent_briefing.md` for the full plan.

## Setup

```
python -m venv .venv
. .venv/Scripts/activate    # bash on Windows
# or: .venv\Scripts\activate   on cmd / PowerShell
pip install -r requirements.txt
cp .env.example .env         # then put your real OPENAI_API_KEY in .env
```

## Layout

- `stem/`   — the stem agent (investigate → propose → test → commit-or-rollback → stop)
- `agent/`  — the specialized-agent runner + the generic baseline
- `tools/`  — starter tools (locked in Phase 0) and per-domain custom tools (written by the stem)
- `evals/`  — probe sets + held-out eval sets per domain
- `configs/`— stem outputs (one JSON config per domain)
- `runs/`   — per-run logs from stem evolution and eval runs
- `writeup/`— writeup.md (final deliverable)

## Status

Phase 0 only: scaffolding, llm_client with budget enforcement, config schema, 7 starter tools,
runner (single_loop + planner_executor), generic baseline, offline smoke test.
