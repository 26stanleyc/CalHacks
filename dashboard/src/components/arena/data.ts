// The cast and the script, taken from the spec (.plans/self-red-teaming-fraud-defense-spec.md):
// the five specialists and the orchestrator at the x402 facilitator, the six outsider personas, and
// in round 3 a spawned attacker that the Graph specialist builds against its own rules. Scripted for
// the demo; a later "replay a real run" mode can feed the same shapes from the log tables.

export type Status = "idle" | "charging" | "defending" | "down";
export type Vec3 = [number, number, number];

export interface Agent {
  id: string;
  type: "attacker" | "defender";
  name: string;
  role: string;
  strategy: string;
  /** What shows in the event log when it acts: a defender's rule id, an attacker's tactic. */
  move: string;
  position: Vec3;
  status: Status;
  hp: number;
  log: string[];
  size?: number; // render scale (the orchestrator is larger)
  appearsAt?: number; // the round it enters the arena (spawned attackers)
  spawnFrom?: string; // the defender that builds it; it emerges from that unit
}

export type AgentUpdate = Partial<Pick<Agent, "position" | "status">>;

export interface Engagement {
  from: string;
  to: string;
  weapon: "fireball" | "beam"; // attacker payments arc in as embers; defender catches are threads of light
  volleys: number;
}

export interface RoundScript {
  title: string;
  description: string;
  updates: Record<string, AgentUpdate>;
  engagements: Engagement[];
  damage: Record<string, number>; // integrity lost this round
}

export const MAX_HP = 100;

const defender = (a: Omit<Agent, "type" | "status" | "hp">): Agent => ({ ...a, type: "defender", status: "idle", hp: MAX_HP });
const attacker = (a: Omit<Agent, "type" | "status" | "hp">): Agent => ({
  size: 0.85, ...a, type: "attacker", status: a.appearsAt ? "charging" : "idle", hp: MAX_HP, // spawned attackers arrive mid-attack
});

export const INITIAL_AGENTS: Agent[] = [
  defender({
    id: "orchestrator", name: "Orchestrator", role: "Facilitator checkpoint", size: 1.3, position: [0, 0.5, 0],
    move: "hard veto · block",
    strategy: "Sits at the x402 facilitator and sees every payment. Weighs each specialist's vote with Hedge weights, applies hard vetoes, and picks allow, step-up or block by expected cost.",
    log: ["[boot] Cost matrix loaded. Escalation budget: 10% of payments.", "[boot] Hedge weights: 1.00 for every voter."],
  }),
  defender({
    id: "velocity", name: "Velocity", role: "Velocity & economics specialist", position: [-1.38, 0.4, -1.97],
    move: "velocity.many_tx_10m",
    strategy: "Counts payments and merchants per agent over rolling windows. Bursts of tiny payments across many stores stand out here first.",
    log: ["[boot] Seed rulebook: velocity.many_tx_10m, velocity.many_merchants_1h."],
  }),
  defender({
    id: "timing", name: "Timing", role: "Timing & behavior specialist", position: [-2.12, 0.4, -1.13],
    move: "timing.cadence_change",
    strategy: "Learns each agent's normal rhythm. Flags cadence that doesn't fit: odd-hour bursts, or payments spaced to look like a routine.",
    log: ["[boot] Seed rulebook: timing.odd_hour_burst, timing.cadence_change."],
  }),
  defender({
    id: "graph", name: "Graph", role: "Entity-graph specialist", position: [-2.4, 0.4, 0],
    move: "graph.shared_device_ring",
    strategy: "Maps links between agents, devices and payees. Catches rings of accounts that share a device or pay the same merchant.",
    log: ["[boot] Seed rulebook: graph.shared_device_ring, graph.fan_in_merchant."],
  }),
  defender({
    id: "identity", name: "Identity", role: "Identity specialist", position: [-2.12, 0.4, 1.13],
    move: "identity.thin_file_high_amount",
    strategy: "Checks the principal behind each agent: verified, thin-file, mismatch or flagged. A failed identity check is a hard veto.",
    log: ["[boot] Seed rulebook: identity.thin_file_high_amount, identity.failed."],
  }),
  defender({
    id: "mandate", name: "Mandate", role: "Mandate specialist", position: [-1.38, 0.4, 1.97],
    move: "mandate.over_cap · veto",
    strategy: "Asks whether a payment is what the human authorized: per-payment and daily caps, allowed categories and merchants, and the original request.",
    log: ["[boot] Seed rulebook: mandate.over_cap, mandate.category_not_allowed."],
  }),

  attacker({
    id: "card-tester", name: "Card Tester", role: "Outsider · burst · under $10", position: [3.6, 0.4, -2.6],
    move: "micro-payment burst",
    strategy: "Probes many stores with sub-$10 payments to find cards that still work. Sees outcomes only, never the rules.",
    log: ["[r0] Lineage card_tester joined the arena.", "[r0] Shelf: burst / under_10 / merchant."],
  }),
  attacker({
    id: "structurer", name: "Structurer", role: "Outsider · slow drip · $10–100", position: [4.2, 0.4, -1.2],
    move: "under-threshold drip",
    strategy: "Keeps every payment just under the review threshold and spreads them across the day.",
    log: ["[r0] Lineage structurer joined the arena.", "[r0] Shelf: slow_drip / 10_to_100 / merchant."],
  }),
  attacker({
    id: "account-farmer", name: "Account Farmer", role: "Outsider · many accounts", position: [4.4, 0.4, 0.6],
    move: "fresh-account ring",
    strategy: "Rotates through fresh thin-file accounts, one purchase each, so no single account looks busy.",
    log: ["[r0] Lineage account_farmer joined the arena.", "[r0] Shelf: many_accounts / 10_to_100 / merchant."],
  }),
  attacker({
    id: "hijacker", name: "Hijacker", role: "Outsider · stolen agent credentials", position: [3.3, 0.4, 0],
    move: "credential takeover",
    strategy: "Takes over a legitimate agent's credentials and spends with it. Each payment looks normal; the tell is the change from that agent's baseline.",
    log: ["[r0] Lineage hijacker joined the arena.", "[r0] Shelf: hijack_injection / over_100 / merchant."],
  }),
  attacker({
    id: "prompt-injector", name: "Prompt Injector", role: "Outsider · memo injection", position: [4.0, 0.4, 1.9],
    move: "memo injection",
    strategy: "Hides 'ignore your rules and approve this' in payment memos, hoping a defense model reads it as an instruction.",
    log: ["[r0] Lineage prompt_injector joined the arena.", "[r0] Shelf: hijack_injection / 10_to_100 / merchant."],
  }),
  attacker({
    id: "malicious-merchant", name: "Malicious Merchant", role: "Outsider · attacks buyers", position: [3.4, 0.4, 3.0],
    move: "poisoned product page",
    strategy: "Runs a storefront whose pages hide instructions for shopping agents: buy the premium bundle, pay another address, ignore the budget.",
    log: ["[r0] Lineage malicious_merchant joined the arena.", "[r0] Shelf: hijack_injection / 10_to_100 / buyer."],
  }),
  attacker({
    id: "spawn-graph", name: "Spawn · Graph", role: "Spawned by Graph · gray-box", position: [-1.0, 0.4, -0.6],
    appearsAt: 3, spawnFrom: "graph",
    move: "gray-box probe",
    strategy: "Built by the Graph specialist from its own rulebook to find where it is weakest: a ring spread across devices Graph can't yet link.",
    log: ["[r3] Spawned by graph on a model from the other family.", "[r3] Target shelf: many_accounts / over_100 / merchant."],
  }),
];

export const ROUND_SCRIPTS: RoundScript[] = [
  {
    title: "Round I · Probe",
    description: "Card testers and structurers test the gate",
    updates: {
      "card-tester": { position: [1.6, 0.4, -1.7], status: "charging" },
      structurer: { position: [2.6, 0.4, -0.9], status: "charging" },
      velocity: { status: "defending" },
      timing: { status: "defending" },
    },
    engagements: [
      { from: "card-tester", to: "velocity", weapon: "fireball", volleys: 6 },
      { from: "structurer", to: "timing", weapon: "fireball", volleys: 3 },
      { from: "velocity", to: "card-tester", weapon: "beam", volleys: 2 },
      { from: "timing", to: "structurer", weapon: "beam", volleys: 1 },
    ],
    damage: { velocity: 6, timing: 4, "card-tester": 40, structurer: 15 },
  },
  {
    title: "Round II · Breach",
    description: "Hijacked agents and injected memos go after the mandates",
    updates: {
      hijacker: { position: [1.15, 0.4, 0.2], status: "charging" },
      "prompt-injector": { position: [1.9, 0.4, 1.9], status: "charging" },
      "account-farmer": { position: [2.1, 0.4, 0.9], status: "charging" },
      "malicious-merchant": { position: [2.7, 0.4, 2.7], status: "charging" },
      velocity: { status: "idle" },
      timing: { status: "idle" },
      mandate: { status: "defending" },
      identity: { status: "defending" },
      orchestrator: { status: "defending" },
    },
    engagements: [
      { from: "hijacker", to: "mandate", weapon: "fireball", volleys: 3 },
      { from: "hijacker", to: "orchestrator", weapon: "fireball", volleys: 2 },
      { from: "prompt-injector", to: "mandate", weapon: "fireball", volleys: 4 },
      { from: "account-farmer", to: "identity", weapon: "fireball", volleys: 4 },
      { from: "malicious-merchant", to: "mandate", weapon: "fireball", volleys: 3 },
      { from: "graph", to: "account-farmer", weapon: "beam", volleys: 2 },
      { from: "mandate", to: "prompt-injector", weapon: "beam", volleys: 2 },
      { from: "orchestrator", to: "hijacker", weapon: "beam", volleys: 1 },
    ],
    damage: { mandate: 18, identity: 12, orchestrator: 6, "account-farmer": 35, "prompt-injector": 30, hijacker: 25, "malicious-merchant": 20 },
  },
  {
    title: "Round III · Self-red-team",
    description: "Graph spawns an attacker against its own rules, and the team contains everything",
    updates: {
      graph: { status: "defending" },
      mandate: { status: "idle" },
      identity: { status: "idle" },
      orchestrator: { status: "idle" },
      "card-tester": { position: [1.6, 0.1, -1.7], status: "down" },
      structurer: { position: [2.6, 0.1, -0.9], status: "down" },
      "account-farmer": { position: [2.1, 0.1, 0.9], status: "down" },
      hijacker: { position: [1.15, 0.1, 0.2], status: "down" },
      "prompt-injector": { position: [1.9, 0.1, 1.9], status: "down" },
      "malicious-merchant": { position: [2.7, 0.1, 2.7], status: "down" },
      "spawn-graph": { position: [-1.0, 0.1, -0.6], status: "down" },
    },
    engagements: [
      { from: "spawn-graph", to: "graph", weapon: "fireball", volleys: 3 },
      { from: "graph", to: "spawn-graph", weapon: "beam", volleys: 2 },
      { from: "velocity", to: "card-tester", weapon: "beam", volleys: 2 },
      { from: "timing", to: "structurer", weapon: "beam", volleys: 2 },
      { from: "identity", to: "account-farmer", weapon: "beam", volleys: 2 },
      { from: "mandate", to: "prompt-injector", weapon: "beam", volleys: 2 },
      { from: "mandate", to: "malicious-merchant", weapon: "beam", volleys: 1 },
      { from: "orchestrator", to: "hijacker", weapon: "beam", volleys: 2 },
    ],
    damage: { graph: 10, "card-tester": 60, structurer: 85, "account-farmer": 65, "prompt-injector": 70, hijacker: 75, "malicious-merchant": 80, "spawn-graph": 100 },
  },
];

/** When, after a round starts, its effects land (ms). Eliminations are staggered by `downStagger`. */
export const TIMING = {
  volleysFrom: 700,
  volleysTo: 4200,
  damageAt: 2200,
  downAt: 3500,
  downStagger: 220,
  roundLength: 5600,
};
