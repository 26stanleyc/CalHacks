# PaySim demo: TagTeam vs a live XGBoost on the same attackers

`configs.json` holds `at100`, the tuned TagTeam behind the arena's PaySim demo (knobs from `engine/tune.py`).
`run_one.py <config> <seed> <rounds> configs.json <out_dir>` runs one config on one seed with the arena export,
with `LLM_PROVIDER=claude` (Claude stand-ins for the open models, `ANTHROPIC_API_KEY` in `.env`) or without it
for the open models. Run it from the repo root: `uv run python evidence/paysim-demo/run_one.py at100 101 20 evidence/paysim-demo/configs.json <out_dir>`.

- `demo-20-rounds-s101.json`: the run checked in as `arena/public/paysim/` (Claude stand-ins, seed 101, 20 rounds).
- `fresh-seeds/`: seeds 101-103, 6 rounds each, Claude stand-ins.
- `open-models/3-rounds-s101.json`: the open models, seed 101, 3 rounds.
