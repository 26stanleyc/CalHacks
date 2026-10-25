import { useEffect, useState } from "react";
import { toBaselineReplay } from "./data/baseline";
import { makeMockReplay } from "./data/mock";
import type { Baseline, Replay } from "./data/types";
import { useArena, type Runs } from "./store";
import { Hud, StageBanner } from "./ui/Hud";
import { ShelfMap } from "./ui/ShelfMap";
import { Timeline } from "./ui/Timeline";
import { UnitCard } from "./ui/UnitCard";
import { setCurrency } from "./ui/words";
import { Scene } from "./world/Scene";

async function loadJson<T>(file: string): Promise<T | null> {
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}${file}`);
    if (res.ok && res.headers.get("content-type")?.includes("json")) return await res.json();
    // A real-data replay is too big to keep as plain JSON, so it's checked in gzipped. The server may
    // already have unzipped it (Content-Encoding), so look for gzip's magic bytes before unzipping.
    if (res.ok && file.endsWith(".gz")) {
      const bytes = new Uint8Array(await res.arrayBuffer());
      const zipped = bytes[0] === 0x1f && bytes[1] === 0x8b;
      const body = zipped ? new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip")) : new Blob([bytes]).stream();
      return JSON.parse(await new Response(body).text());
    }
  } catch {
    // missing or unreadable: the caller decides what to do
  }
  return null;
}

// The two data sets the arena can play. "sim" is the default: public/replay.json from our simulated
// marketplace (or the mock run when there's none). "real" is a public fraud dataset run through the same
// defense by engine/public_replay.py, in public/real/. Each folder's baseline.json adds step 0: the same
// traffic, decided by the baseline.
type DataSet = "sim" | "real";
const FILES: Record<DataSet, { replay: string; baseline: string }> = {
  sim: { replay: "replay.json", baseline: "baseline.json" },
  real: { replay: "real/replay.json.gz", baseline: "real/baseline.json" },
};

async function loadRuns(data: DataSet): Promise<Runs> {
  const after = (await loadJson<Replay>(FILES[data].replay)) ?? makeMockReplay();
  const baseline = await loadJson<Baseline>(FILES[data].baseline);
  if (!baseline || baseline.run_id !== after.run_id) return { after, before: null, baseline: null };
  return { after, before: toBaselineReplay(after, baseline), baseline };
}

const initialData = (): DataSet => (new URLSearchParams(location.search).get("data") === "real" ? "real" : "sim");

// Switches between the simulated marketplace and the real dataset; the choice lives in the URL
// (?data=real) so a reload or a shared link keeps it. Only shown when the real files exist.
function DataSwitch({ data, onChange }: { data: DataSet; onChange: (d: DataSet) => void }) {
  const dataset = useArena((s) => s.runs?.after.dataset); // of the run on screen, which may still be the old one
  const [available, setAvailable] = useState(data === "real");
  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}${FILES.real.baseline}`, { method: "HEAD" })
      .then((res) => setAvailable(res.ok && !!res.headers.get("content-type")?.includes("json")))
      .catch(() => {});
  }, []);
  if (!available) return null;
  return (
    <section className="data-switch" aria-label="Data set">
      <div className="seg" role="group" aria-label="Which payments to play">
        <button aria-pressed={data === "sim"} onClick={() => onChange("sim")}>
          Simulated marketplace
        </button>
        <button aria-pressed={data === "real"} onClick={() => onChange("real")}>
          Real cards: ULB / Worldline
        </button>
      </div>
      {data !== (dataset ? "real" : "sim") ? (
        <p className="source">Loading {data === "real" ? "the real payments" : "the simulated run"}…</p>
      ) : data === "real" && dataset && (
        <p className="source">
          {dataset.source}.{" "}
          <a href={dataset.url} target="_blank" rel="noreferrer">
            Kaggle
          </a>
          , {dataset.license}. Amounts in {dataset.currency}; {dataset.round_hours} hours of payments per round.
        </p>
      )}
    </section>
  );
}

// Two groups: who is on the board, and what the ring under an attacker means.
function Legend() {
  const before = useArena((s) => s.stage === "before");
  const dataset = useArena((s) => !!s.replay?.dataset);
  return (
    <aside className="legend" aria-label="Legend">
      <div className="group">
        <p className="head">On the board</p>
        {before ? <p><i className="icon model" /> XGBoost model</p> : <p><i className="icon angel" /> TagTeam defender</p>}
        <p><i className="icon devil" /> {dataset ? "Fraud, grouped by amount" : "Attacker"}</p>
        <p><i className="icon shopper" /> {dataset ? "Honest cardholder" : "Honest shopper"}</p>
      </div>
      <div className="group">
        <p className="head">Ring under an attacker</p>
        {dataset ? (
          <p><i className="ring dataset" /> Labelled fraud in the dataset</p>
        ) : (
          <>
            <p><i className="ring spawned" /> Built by a defender</p>
            <p><i className="ring outsider" /> Outsider</p>
            <p><i className="ring news" /> From scam news</p>
            <p><i className="ring held_out" /> Held-out test</p>
          </>
        )}
      </div>
    </aside>
  );
}

export default function App() {
  const replay = useArena((s) => s.replay);
  const setRuns = useArena((s) => s.setRuns);
  const [data, setData] = useState<DataSet>(initialData);
  useEffect(() => {
    let current = true;
    loadRuns(data).then((runs) => {
      if (!current) return;
      setCurrency(runs.after.dataset?.currency);
      setRuns(runs);
    });
    return () => {
      current = false;
    };
  }, [data, setRuns]);
  const choose = (d: DataSet) => {
    const url = new URL(location.href);
    if (d === "real") url.searchParams.set("data", "real");
    else url.searchParams.delete("data");
    history.replaceState(null, "", url);
    setData(d);
  };

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
            <DataSwitch data={data} onChange={choose} />
            {/* The grid is our arena's attack taxonomy; a dataset's fraud has amounts but no attack styles. */}
            {!replay.dataset && <ShelfMap />}
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
