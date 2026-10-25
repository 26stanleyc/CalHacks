import { useEffect, useState } from "react";
import { toBaselineReplay } from "./data/baseline";
import { makeMockReplay } from "./data/mock";
import type { Baseline, Replay } from "./data/types";
import { useArena, type Runs } from "./store";
import { Hud, StageBanner } from "./ui/Hud";
import { ShelfMap } from "./ui/ShelfMap";
import { Timeline } from "./ui/Timeline";
import { UnitCard } from "./ui/UnitCard";
import { EthLogo } from "./ui/EthLogo";
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

// The data sets the arena can play. "sim" is the default: public/replay.json from our simulated
// marketplace (or the mock run when there's none). The others are public fraud datasets run through the
// same defense by engine/public_replay.py, each in its own folder. Each folder's baseline.json adds step
// 0: the same traffic, decided by the baseline.
type DataSet = "sim" | "real" | "eth";
const DATA_SETS: Record<DataSet, { label: string; replay: string; baseline: string }> = {
  sim: { label: "Simulated marketplace", replay: "replay.json", baseline: "baseline.json" },
  real: { label: "Real cards: ULB / Worldline", replay: "real/replay.json.gz", baseline: "real/baseline.json" },
  eth: { label: "Real Ethereum phishing", replay: "eth/replay.json.gz", baseline: "eth/baseline.json" },
};
const ALL = Object.keys(DATA_SETS) as DataSet[];

async function loadRuns(data: DataSet): Promise<Runs> {
  const after = (await loadJson<Replay>(DATA_SETS[data].replay)) ?? makeMockReplay();
  const baseline = await loadJson<Baseline>(DATA_SETS[data].baseline);
  if (!baseline || baseline.run_id !== after.run_id) return { after, before: null, baseline: null };
  return { after, before: toBaselineReplay(after, baseline), baseline };
}

// A page can lock the arena to one data set: /ethereum/ is the real Ethereum run as its own demo
// (<body data-data-set="eth">), with no switch.
const LOCKED = document.body.dataset.dataSet as DataSet | undefined;

const initialData = (): DataSet => {
  if (LOCKED) return LOCKED;
  const asked = new URLSearchParams(location.search).get("data");
  return ALL.includes(asked as DataSet) ? (asked as DataSet) : "sim";
};

// Switches between the simulated marketplace and the real datasets (on a locked page, just names the source); the choice lives in the URL
// (?data=eth) so a reload or a shared link keeps it. A dataset is offered only when its files exist,
// and the switch only appears when there's more than one choice.
function DataSwitch({ data, loading, onChange }: { data: DataSet; loading: boolean; onChange: (d: DataSet) => void }) {
  const dataset = useArena((s) => s.runs?.after.dataset);
  const [available, setAvailable] = useState<DataSet[]>(["sim", ...(data === "sim" ? [] : [data])]);
  useEffect(() => {
    Promise.all(
      ALL.filter((d) => d !== "sim").map((d) =>
        fetch(`${import.meta.env.BASE_URL}${DATA_SETS[d].baseline}`, { method: "HEAD" })
          .then((res) => res.ok && !!res.headers.get("content-type")?.includes("json"))
          .catch(() => false),
      ),
    ).then((found) => setAvailable(["sim", ...ALL.filter((d) => d !== "sim").filter((_, i) => found[i])]));
  }, []);
  if (available.length < 2 && !LOCKED) return null;
  return (
    <section className="data-switch" aria-label="Data set">
      {!LOCKED && ( // a locked page keeps only the source line
        <div className="seg" role="group" aria-label="Which payments to play">
          {available.map((d) => (
            <button key={d} aria-pressed={data === d} onClick={() => onChange(d)}>
              {DATA_SETS[d].label}
            </button>
          ))}
        </div>
      )}
      {loading ? (
        <p className="source">Loading the payments…</p>
      ) : (
        dataset && (
          <>
            {dataset.currency === "ETH" && (
              <p className="chain big">
                <EthLogo size={34} />
                <b>
                  Ethereum
                  {dataset.start && (
                    <span>
                      Real mainnet transactions,{" "}
                      {new Date(dataset.start).toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" })}
                    </span>
                  )}
                </b>
              </p>
            )}
            <p className="source">
            {dataset.source}.{" "}
            <a href={dataset.url} target="_blank" rel="noreferrer">
              Source
            </a>
            , {dataset.license}. Amounts in {dataset.currency}; {dataset.round_hours} hours of payments per round.
            </p>
          </>
        )
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
        <p><i className="icon shopper" /> {dataset ? "Honest payer" : "Honest shopper"}</p>
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
  const [shown, setShown] = useState<DataSet | null>(null); // the data set on screen; lags `data` while loading
  useEffect(() => {
    let current = true;
    loadRuns(data).then((runs) => {
      if (!current) return;
      setCurrency(runs.after.dataset?.currency);
      setRuns(runs);
      setShown(data);
    });
    return () => {
      current = false;
    };
  }, [data, setRuns]);
  const choose = (d: DataSet) => {
    const url = new URL(location.href);
    if (d === "sim") url.searchParams.delete("data");
    else url.searchParams.set("data", d);
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
            <DataSwitch data={data} loading={shown !== data} onChange={choose} />
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
