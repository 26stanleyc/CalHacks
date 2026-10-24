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
  held_out: "Held-out exam attacker",
};

export const money = (cents: number) =>
  cents >= 100000
    ? `$${(cents / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}`
    : `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: cents < 1000 ? 2 : 0, maximumFractionDigits: cents < 1000 ? 2 : 0 })}`;

export const modelName = (m: string | null) => (m ? m.split("/")[1] : "hand-written");

export function paramLines(p: Record<string, number>): [string, string][] {
  const out: [string, string][] = [];
  if (p.amount !== undefined) out.push(["Amount", money(p.amount)]);
  if (p.gap_seconds !== undefined)
    out.push(["Gap", p.gap_seconds >= 120 ? `${Math.round(p.gap_seconds / 60)} min` : `${Math.round(p.gap_seconds)} s`]);
  if (p.accounts !== undefined) out.push(["Accounts", String(Math.round(p.accounts))]);
  if (p.burst_size !== undefined && p.burst_size > 1) out.push(["Burst", `${Math.round(p.burst_size)} payments`]);
  return out;
}
