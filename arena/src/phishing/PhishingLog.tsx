// The /phishing/ page: one real phishing scam on Ethereum, payment by payment, as a scrolling log. Each line
// is a real transaction from the test week (public/eth/log.json, made by engine/phishing_log.py from the same
// run the arena plays), with what TagTeam and XGBoost decided.

import { useEffect, useRef, useState } from "react";
import type { Action, DatasetInfo } from "../data/types";
import { EthLogo } from "../ui/EthLogo";
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

const SPEEDS = [1, 2, 4, 8];
const PER_SECOND = 2.5; // log lines at 1x
const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`;
const when = (ts: number) =>
  new Date(ts * 1000).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });
const verb = (v: Verdict) => (v.action === "block" ? "Blocked" : v.action === "step_up" ? (v.settled ? "Checked, paid" : "Checked, stopped") : "Allowed");

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
  const [shown, setShown] = useState(0); // lines played so far
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const feed = useRef<HTMLOListElement>(null);
  const [following, setFollowing] = useState(true); // the log keeps up with new entries only while you're at the bottom

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}eth/log.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((l: Log) => {
        setCurrency(l.dataset.currency);
        setLog(l);
      })
      .catch(() => setFailed(true));
  }, []);
  const rows = log?.lines ?? [];

  useEffect(() => {
    if (!playing || !rows.length) return;
    if (shown >= rows.length) return setPlaying(false);
    const id = setTimeout(() => setShown((n) => n + 1), 1000 / PER_SECOND / speed);
    return () => clearTimeout(id);
  }, [playing, shown, speed, rows.length]);

  useEffect(() => {
    const el = feed.current;
    if (el && following) el.scrollTop = el.scrollHeight; // instant: a smooth scroll would fire scroll events mid-way and read as you scrolling up
  }, [shown, following]);
  const onScroll = () => {
    const el = feed.current;
    if (el) setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 60);
  };

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

  const played = rows.slice(0, shown);
  const scam = played.filter((l) => l.scam);
  const sent = scam.reduce((a, l) => a + l.amount_cents, 0);
  const lostTT = scam.filter((l) => l.tagteam.settled).reduce((a, l) => a + l.amount_cents, 0);
  const lostX = scam.filter((l) => l.xgboost.settled).reduce((a, l) => a + l.amount_cents, 0);
  const stoppedTT = scam.filter((l) => !l.tagteam.settled).length;
  const stoppedX = scam.filter((l) => !l.xgboost.settled).length;
  const restart = () => {
    setFollowing(true);
    setShown(0);
    setPlaying(true);
  };

  return (
    <main className="phishing">
      <header className="phish-head">
        <p className="kicker chain">
          <EthLogo /> Ethereum mainnet <span>One real scam, payment by payment</span>
        </p>
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
        {/* Two logs on one scroll: the same payments, row for row, XGBoost's decisions left and TagTeam's right. */}
        <div className="phish-cols" aria-hidden="true">
          {(["xgb", "tagteam"] as const).map((side) => (
            <div key={side} className={`side ${side}`}>
              <b>{side === "xgb" ? "XGBoost" : "TagTeam"}</b>
              <span>Time (UTC)</span>
              <span>From → to</span>
              <span>Amount</span>
              <span>Decision</span>
            </div>
          ))}
        </div>
        <ol ref={feed} aria-live="polite" onScroll={onScroll}>
          {rows.slice(0, shown).map((line) => (
            <li
              key={line.tx_id}
              className={[
                line.scam ? "scam" : line.phishing ? "other-scam" : "",
                line.phishing && !line.tagteam.settled && line.xgboost.settled ? "caught" : "",
              ].join(" ")}
            >
              {(["xgb", "tagteam"] as const).map((side) => {
                const v = side === "xgb" ? line.xgboost : line.tagteam;
                return (
                  <div key={side} className={`side ${side}`}>
                    <span className="time">
                      {when(line.ts)}
                      {line.scam && <i className="tag">scam</i>}
                    </span>
                    <span className="addr">
                      {short(line.from)} → {short(line.to)}
                    </span>
                    <span className="amount">{money(line.amount_cents)}</span>
                    <span className={`verdict ${v.settled ? "through" : "stopped"}${line.phishing ? " fraud" : ""}`}>
                      {verb(v)}
                      {line.phishing && v.settled && <small>{money(line.amount_cents)} lost</small>}
                      {side === "tagteam" && !v.settled && line.phishing && (
                        <small>
                          {line.reports} prior {line.reports === 1 ? "report" : "reports"}
                          {reasons(v.why).length ? `: ${reasons(v.why).join(", ")}` : ""}
                        </small>
                      )}
                      {side === "tagteam" && line.phishing && !v.settled && line.xgboost.settled && (
                        <i className="caught-tag">XGBoost missed this</i>
                      )}
                    </span>
                  </div>
                );
              })}
            </li>
          ))}
        </ol>
        {!following && (
          <button className="latest" onClick={() => setFollowing(true)}>
            Jump to latest
          </button>
        )}
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
