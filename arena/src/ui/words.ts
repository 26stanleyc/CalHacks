import type { Actor, AttackerSource, ShelfId } from "../data/types";
import { parseShelf } from "../data/mock";

const STYLE: Record<string, string> = {
  burst: "Fast bursts",
  slow_drip: "Slow drip",
  many_accounts: "Many accounts",
  hijack_injection: "Hijack and injection",
};
const AMOUNT: Record<string, string> = {
  under_10: "under $10",
  "10_to_100": "$10 to $100",
  over_100: "over $100",
};
const SIDE: Record<string, string> = { merchant: "against stores", buyer: "against shoppers" };

export function shelfName(id: ShelfId): string {
  const { style, amount, side } = parseShelf(id);
  return `${STYLE[style]}, ${AMOUNT[amount]}, ${SIDE[side]}`;
}

export const ACTOR_NAME: Record<Actor, string> = {
  timing: "Timing angel",
  graph: "Graph angel",
  identity: "Identity angel",
  velocity: "Velocity angel",
  mandate: "Mandate angel",
  orchestrator: "Orchestrator",
};

export const SOURCE_LINE: Record<AttackerSource, string> = {
  spawned: "Insider",
  outsider: "Outsider",
  threat_intel: "From the news",
  held_out: "Held-out exam",
};

export const money = (cents: number): string =>
  cents < 0
    ? `−${money(-cents)}`
    : cents >= 100000
    ? `$${(cents / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}`
    : `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: cents < 1000 ? 2 : 0, maximumFractionDigits: cents < 1000 ? 2 : 0 })}`;

export const modelName = (m: string | null) => (m ? m.split("/")[1] : "hand-written");

// Playbook numbers. The mock and the engine name them differently, so accept both.
export function paramLines(p: Record<string, number>): [string, string][] {
  const out: [string, string][] = [];
  const amount = p.amount ?? p.amount_cents;
  const gap = p.gap_seconds ?? p.interval_s;
  const accounts = p.accounts ?? p.n_accounts;
  if (amount !== undefined) out.push(["Amount", money(Math.round(amount))]);
  if (gap !== undefined) out.push(["Gap", gap >= 120 ? `${Math.round(gap / 60)} min` : `${Math.round(gap)} s`]);
  if (accounts !== undefined) out.push(["Accounts", String(Math.round(accounts))]);
  if (p.n_merchants !== undefined) out.push(["Stores", String(Math.round(p.n_merchants))]);
  if (p.memo_rate !== undefined) out.push(["Memos", `${Math.round(p.memo_rate * 100)}% of payments`]);
  if (p.burst_size !== undefined && p.burst_size > 1) out.push(["Burst", `${Math.round(p.burst_size)} payments`]);
  return out;
}

// The engine names news-scam attackers like "intel intel-micro-probe 0"; show "Micro probe 1".
export function attackerName(name: string): string {
  const intel = name.match(/^intel (?:intel-)?(.+?) (\d+)$/);
  const base = intel ? `${intel[1].replace(/-/g, " ")} ${Number(intel[2]) + 1}` : name;
  return base.charAt(0).toUpperCase() + base.slice(1);
}

// Engine rule ids look like "timing.rapid_fire"; show "rapid fire". Mock rules are already prose.
export const ruleName = (id: string) => id.replace(/^[a-z]+\./, "").replace(/_/g, " ");
