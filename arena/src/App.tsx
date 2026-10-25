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

// Plays public/replay.json when the engine has exported one, otherwise the mock run. A
// public/baseline.json for the same run adds step 0: the same traffic, decided by the baseline.
async function loadRuns(): Promise<Runs> {
  const after = (await loadJson<Replay>("replay.json")) ?? makeMockReplay();
  const baseline = await loadJson<Baseline>("baseline.json");
  if (!baseline || baseline.run_id !== after.run_id) return { after, before: null, baseline: null };
  return { after, before: toBaselineReplay(after, baseline), baseline };
}

function Legend() {
  const before = useArena((s) => s.stage === "before");
  return (
    <aside className="legend" aria-label="Legend">
      {before ? <p><i className="dot model" /> XGBoost model</p> : <p><i className="dot angel" /> Angel defender</p>}
      <p><i className="dot devil" /> Devil attacker</p>
      <p><i className="dot shopper" /> Honest shopper</p>
      {!before && <p><i className="ring spawned" /> Built by an angel</p>}
      <p><i className="ring outsider" /> Outsider</p>
      <p><i className="ring news" /> From the news</p>
      <p><i className="ring held_out" /> Exam attacker</p>
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
