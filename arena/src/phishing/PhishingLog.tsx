// The /phishing/ page: one real phishing scam on Ethereum, payment by payment, as a scrolling log. Each line
// is a real transaction from the test week (public/eth/log.json, made by engine/phishing_log.py from the same
// run the arena plays), with what TagTeam and XGBoost decided. Between days, the night's learning shows: the
// scam's confirmed reports reach TagTeam's memory; XGBoost's memory stays where its training left it.

import { useEffect, useRef, useState } from "react";
import type { Action, DatasetInfo } from "../data/types";
import { money, ruleName, setCurrency } from "../ui/words";

interface Verdict {
  action: Action;
  settled: boolean;
  why?: string;
}
interface Line {
  tx_id: string;
  ts: number;
  from: string;
  to: string;
  amount_cents: number;
  phishing: boolean;
  scam: boolean; // into the address this page follows
  tagteam: Verdict;
  xgboost: Verdict;
  reports: number; // the receiver's confirmed phishing reports TagTeam knew of
  reports_frozen: number; // the same, as XGBoost's memory had it (frozen at the start of the week)
}
interface Log {
  run_id: string;
  dataset: DatasetInfo;
  address: string;
  victims: number;
  context: number;
  lines: Line[];
}

type Row = { kind: "line"; line: Line } | { kind: "night"; day: string; reports: number; frozen: number; key: string };

const SPEEDS = [1, 2, 4, 8];
const PER_SECOND = 2.5; // log lines at 1x
const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`;
const utc = (ts: number, opts: Intl.DateTimeFormatOptions) => new Date(ts * 1000).toLocaleString("en-US", { ...opts, timeZone: "UTC" });
const when = (ts: number) => utc(ts, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const verb = (v: Verdict) => (v.action === "block" ? "Blocked" : v.action === "step_up" ? (v.settled ? "Checked, paid" : "Checked, stopped") : "Allowed");

// Rows to play: the log's lines, with a night row wherever a new day starts and TagTeam's memory of the scam grew.
function rowsOf(log: Log): Row[] {
  const rows: Row[] = [];
  let day = "";
  let known = -1;
  for (const line of log.lines) {
    const d = utc(line.ts, { month: "short", day: "numeric" });
    if (d !== day) {
      const scamLine = log.lines.find((l) => l.scam && utc(l.ts, { month: "short", day: "numeric" }) === d);
      if (scamLine && scamLine.reports !== known && day) rows.push({ kind: "night", day, reports: scamLine.reports, frozen: scamLine.reports_frozen, key: `night-${d}` });
      if (scamLine) known = scamLine.reports;
      day = d;
    }
    rows.push({ kind: "line", line });
  }
  return rows;
}

// The rule ids the explanation names, in words: "Blocked: graph.receiver_reported_positive." -> "receiver reported positive".
const reasons = (why = "") =>
  why
    .replace(/^[^:]*:\s*/, "")
    .replace(/\.$/, "")
    .split(/,\s*/)
    .filter((r) => r.includes("."))
    .map(ruleName);

export function PhishingLog() {
  const [log, setLog] = useState<Log | null>(null);
  const [failed, setFailed] = useState(false);
  const [shown, setShown] = useState(0); // rows played so far
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const feed = useRef<HTMLOListElement>(null);

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}eth/log.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((l: Log) => {
        setCurrency(l.dataset.currency);
        setLog(l);
      })
      .catch(() => setFailed(true));
  }, []);
  const rows = log ? rowsOf(log) : [];

  useEffect(() => {
    if (!playing || !rows.length) return;
    if (shown >= rows.length) return setPlaying(false);
    // A night pauses a moment longer, so the learning reads.
    const wait = (rows[shown]?.kind === "night" ? 2200 : 1000 / PER_SECOND) / speed;
    const id = setTimeout(() => setShown((n) => n + 1), wait);
    return () => clearTimeout(id);
  }, [playing, shown, speed, rows.length]);

  useEffect(() => {
    feed.current?.lastElementChild?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [shown]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === " " && !(e.target instanceof HTMLButtonElement)) {
        e.preventDefault();
        setPlaying((p) => !p);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (failed) return <p className="loading">No phishing log yet (public/eth/log.json is missing).</p>;
  if (!log) return <p className="loading">Loading the payment log</p>;

  const played = rows.slice(0, shown).flatMap((r) => (r.kind === "line" ? [r.line] : []));
  const scam = played.filter((l) => l.scam);
  const sent = scam.reduce((a, l) => a + l.amount_cents, 0);
  const lostTT = scam.filter((l) => l.tagteam.settled).reduce((a, l) => a + l.amount_cents, 0);
  const lostX = scam.filter((l) => l.xgboost.settled).reduce((a, l) => a + l.amount_cents, 0);
  const stoppedTT = scam.filter((l) => !l.tagteam.settled).length;
  const stoppedX = scam.filter((l) => !l.xgboost.settled).length;
  const restart = () => {
    setShown(0);
    setPlaying(true);
  };

  return (
    <main className="phishing">
      <header className="phish-head">
        <p className="kicker">One real scam, payment by payment</p>
        <h1>
          Phishing address <code>{short(log.address)}</code>
        </h1>
        <p className="lead">
          Etherscan later labelled this address as phishing. Below are its victims' payments as they happened in March 2018,
          mixed with the {log.context} ordinary payments just before each, and what each defense did. Nobody knew it was a scam
          until the first victims reported it.
        </p>
        <dl className="phish-score">
          <div>
            <dt>Paid to the scam so far</dt>
            <dd>
              {money(sent)} <small>{scam.length} of {log.victims} payments</small>
            </dd>
          </div>
          <div className="tagteam">
            <dt>TagTeam stopped</dt>
            <dd>
              {stoppedTT} <small>lost {money(lostTT)}</small>
            </dd>
          </div>
          <div className="xgb">
            <dt>XGBoost stopped</dt>
            <dd>
              {stoppedX} <small>lost {money(lostX)}</small>
            </dd>
          </div>
        </dl>
      </header>

      <section className="phish-log" aria-label="Payment log">
        <div className="phish-cols" aria-hidden="true">
          <span>Time (UTC)</span>
          <span>From</span>
          <span>To</span>
          <span>Amount</span>
          <span>TagTeam</span>
          <span>XGBoost</span>
        </div>
        <ol ref={feed} aria-live="polite">
          {rows.slice(0, shown).map((r) =>
            r.kind === "night" ? (
              <li key={r.key} className="night">
                <b>Night after {r.day}.</b> The day's payments are confirmed. TagTeam learns them: {short(log.address)} now has{" "}
                <b>
                  {r.reports} confirmed phishing {r.reports === 1 ? "report" : "reports"}
                </b>{" "}
                in its memory. XGBoost was trained once, before this week: its memory still holds {r.frozen}.
              </li>
            ) : (
              <li key={r.line.tx_id} className={r.line.scam ? "scam" : r.line.phishing ? "other-scam" : ""}>
                <span className="time">{when(r.line.ts)}</span>
                <span className="addr">{short(r.line.from)}</span>
                <span className="addr">
                  {short(r.line.to)}
                  {r.line.scam && <i className="tag">scam</i>}
                </span>
                <span className="amount">{money(r.line.amount_cents)}</span>
                <span className={`verdict ${r.line.tagteam.settled ? "through" : "stopped"}${r.line.phishing ? " fraud" : ""}`}>
                  {verb(r.line.tagteam)}
                  {!r.line.tagteam.settled && r.line.phishing && (
                    <small>
                      {r.line.reports} prior {r.line.reports === 1 ? "report" : "reports"}
                      {reasons(r.line.tagteam.why).length ? `: ${reasons(r.line.tagteam.why).join(", ")}` : ""}
                    </small>
                  )}
                </span>
                <span className={`verdict ${r.line.xgboost.settled ? "through" : "stopped"}${r.line.phishing ? " fraud" : ""}`}>
                  {verb(r.line.xgboost)}
                </span>
              </li>
            ),
          )}
        </ol>
      </section>

      <footer className="phish-controls">
        <button className="play" onClick={() => (shown >= rows.length ? restart() : setPlaying(!playing))} aria-label={playing ? "Pause" : "Play"}>
          {playing ? (
            <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="4" y="3" width="4" height="14" rx="1.5" /><rect x="12" y="3" width="4" height="14" rx="1.5" /></svg>
          ) : (
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 3.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 5 3.5z" /></svg>
          )}
        </button>
        <p className="source">
          Real transactions from {log.dataset.name} ({log.dataset.license}); decisions from the run the Real data arena plays (
          {log.run_id}). Red rows are payments into an address labelled phishing; the scam this page follows is tagged.
        </p>
        <div className="speeds" role="group" aria-label="Playback speed">
          {SPEEDS.map((s) => (
            <button key={s} className={s === speed ? "on" : ""} onClick={() => setSpeed(s)} aria-pressed={s === speed}>
              {s}×
            </button>
          ))}
        </div>
      </footer>
    </main>
  );
}
