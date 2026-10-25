import { useEffect } from "react";
import { toBaselineReplay } from "./data/baseline";
import { makeMockReplay } from "./data/mock";
import type { Baseline, Replay } from "./data/types";
import { useArena, type Runs } from "./store";
import { Hud, StageBanner } from "./ui/Hud";
import { ShelfMap } from "./ui/ShelfMap";
import { Timeline } from "./ui/Timeline";
import { UnitCard } from "./ui/UnitCard";
import { Scene } from "./world/Scene";

async function loadJson<T>(file: string): Promise<T | null> {
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}${file}`);
    if (res.ok && res.headers.get("content-type")?.includes("json")) return await res.json();
  } catch {
    // missing or unreadable: the caller decides what to do
  }
  return null;
}

// Engine exports number waves from 0; the arena's clock times them from 1.
const renumber = (replay: Replay) => replay.rounds.forEach((r) => r.waves.forEach((w, i) => (w.wave = i + 1)));

// The demo's data set, kept in ?data=: "paysim" (the default) or "marketplace" (the simulated one).
// PaySim: public/paysim/replay.json is TagTeam's run and public/paysim/xgboost.json XGBoost's own live
// run against the same attackers (engine/live_vs_xgboost.py --world paysim --export).
// Marketplace: public/replay.json, or the mock run, with public/baseline.json for the same run adding
// step 0: the same traffic, decided by the baseline.
async function loadRuns(): Promise<Runs> {
  if (new URLSearchParams(location.search).get("data") !== "marketplace") {
    const after = await loadJson<Replay>("paysim/replay.json");
    const before = await loadJson<Replay & { label?: string }>("paysim/xgboost.json");
    if (after && before) {
      renumber(after);
      renumber(before);
      before.rounds.forEach((r) => (r.metrics = { ...r.metrics, exploitability_cents: -1, frozen: false }));
      const zero = { total_cost_cents: 0, fraud_cost_cents: 0, honest_cost_cents: 0, mean_fp: 0, max_fp: 0 };
      const baseline: Baseline = {
        run_id: before.run_id, contender: before.defense, label: before.label ?? "XGBoost, retrained each round",
        trained_on: "Retrained on every label after each round, and attacked live by the same attackers as TagTeam",
        decisions: {}, totals: zero, ours_totals: zero, live: true,
      };
      return { after, before, baseline, dataset: "paysim" };
    }
  }
  const after = (await loadJson<Replay>("replay.json")) ?? makeMockReplay();
  renumber(after);
  const baseline = await loadJson<Baseline>("baseline.json");
  if (!baseline || baseline.run_id !== after.run_id) return { after, before: null, baseline: null, dataset: "marketplace" };
  return { after, before: toBaselineReplay(after, baseline), baseline, dataset: "marketplace" };
}

// Two groups: who is on the board, and what the ring under an attacker means.
function Legend() {
  const before = useArena((s) => s.stage === "before");
  const paysim = useArena((s) => s.runs?.dataset === "paysim");
  return (
    <aside className="legend" aria-label="Legend">
      <div className="group">
        <p className="head">On the board</p>
        {before ? <p><i className="icon model" /> XGBoost model</p> : <p><i className="icon angel" /> TagTeam defender</p>}
        <p><i className="icon devil" /> Attacker</p>
        <p><i className="icon shopper" /> {paysim ? "Honest payment (PaySim)" : "Honest shopper"}</p>
      </div>
      <div className="group">
        <p className="head">Ring under an attacker</p>
        <p><i className="ring spawned" /> Built by a defender</p>
        <p><i className="ring outsider" /> Outsider</p>
        <p><i className="ring news" /> From scam news</p>
        <p><i className="ring held_out" /> Held-out test</p>
      </div>
    </aside>
  );
}

export default function App() {
  const replay = useArena((s) => s.replay);
  const setRuns = useArena((s) => s.setRuns);
  useEffect(() => {
    loadRuns().then(setRuns);
  }, [setRuns]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const s = useArena.getState();
      if (e.key === " " && !(e.target instanceof HTMLButtonElement)) {
        e.preventDefault();
        s.toggle();
      } else if (e.key === "Escape") s.select(null);
      else if (e.key === "ArrowRight") s.step(1);
      else if (e.key === "ArrowLeft") s.step(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <main className="arena">
      {replay ? (
        <>
          <Scene />
          <Hud />
          <StageBanner />
          <div className="hud-right">
            <ShelfMap />
            <UnitCard />
          </div>
          <Legend />
          <Timeline />
        </>
      ) : (
        <p className="loading">Setting up the arena</p>
      )}
    </main>
  );
}
