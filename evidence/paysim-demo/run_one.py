"""The PaySim demo runs (arena/public/paysim/): one faithful Claude-stand-in run (LLM_PROVIDER=claude) of a named config on one seed, with arena export."""
import json, sys, time
from pathlib import Path

import integrations.settings  # noqa: F401  (loads .env)
from engine.live_vs_xgboost import run
from engine.tune import KNOBS

name, seed, rounds, configs, out_dir = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], Path(sys.argv[5])
config = json.loads(Path(configs).read_text())[name]
for k, v in config.items():
    KNOBS[k](v)
started = time.monotonic()
out = run(seed, rounds, real=True, configure=lambda e: [KNOBS[k](v, e) for k, v in config.items()], world="paysim",
          export_dir=out_dir / f"{name}-s{seed}")
out.update({"name": name, "config": config, "seed": seed, "seconds": round(time.monotonic() - started)})
(out_dir / f"{name}-s{seed}.json").write_text(json.dumps(out, indent=1))
t, x = out["tagteam_live"], out["xgboost_live"]
r2 = 1 - sum(r["cost"] for r in t["per_round"][1:]) / sum(r["cost"] for r in x["per_round"][1:])
print(name, seed, "roster", out["same_roster_every_round"], f"rounds 2+ cost better {100*r2:.1f}%", out["investigator"] | {"seconds": None})
