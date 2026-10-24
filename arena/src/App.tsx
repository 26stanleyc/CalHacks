import { useEffect } from "react";
import { makeMockReplay } from "./data/mock";
import type { Replay } from "./data/types";
import { useArena } from "./store";
import { Hud } from "./ui/Hud";
import { Timeline } from "./ui/Timeline";
import { UnitCard } from "./ui/UnitCard";
import { Scene } from "./world/Scene";

// Plays public/replay.json when the engine has exported one, otherwise the mock run.
async function loadReplay(): Promise<Replay> {
  try {
    const res = await fetch("replay.json");
    if (res.ok && res.headers.get("content-type")?.includes("json")) return await res.json();
  } catch {
    // fall through to the mock
  }
  return makeMockReplay();
}

function Legend() {
  return (
    <aside className="legend" aria-label="Legend">
      <p><i className="dot angel" /> Angel defender</p>
      <p><i className="dot devil" /> Devil attacker</p>
      <p><i className="dot shopper" /> Honest shopper</p>
      <p><i className="ring spawned" /> Built by an angel</p>
      <p><i className="ring outsider" /> Outsider</p>
      <p><i className="ring news" /> From the news</p>
      <p><i className="tile shaky" /> Shelf leaking</p>
      <p><i className="tile held" /> Shelf held</p>
    </aside>
  );
}

export default function App() {
  const replay = useArena((s) => s.replay);
  const setReplay = useArena((s) => s.setReplay);
  useEffect(() => {
    loadReplay().then(setReplay);
  }, [setReplay]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const s = useArena.getState();
      if (e.key === " " && !(e.target instanceof HTMLButtonElement)) {
        e.preventDefault();
        s.toggle();
      } else if (e.key === "Escape") s.select(null);
      else if (e.key === "ArrowRight" && s.replay) s.jump(Math.min(s.round + 1, s.replay.rounds.length - 1));
      else if (e.key === "ArrowLeft") s.jump(Math.max(s.round - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!replay) return <div className="loading">Setting up the arena</div>;
  return (
    <main className="arena">
      <Scene />
      <Hud />
      <UnitCard />
      <Legend />
      <Timeline />
    </main>
  );
}
