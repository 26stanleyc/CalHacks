// The replay format the arena plays back. One JSON file per run.
// Every field maps to a column in contracts/db.sql, so an exporter can build it with one
// query per table per round; the table each block comes from is noted alongside it.

export type Lane = "timing" | "graph" | "identity" | "velocity" | "mandate";
export type Actor = Lane | "orchestrator";
export type Action = "allow" | "step_up" | "block";
export type AttackStyle = "burst" | "slow_drip" | "many_accounts" | "hijack_injection";
export type AmountBand = "under_10" | "10_to_100" | "over_100";
export type Side = "merchant" | "buyer";
// "dataset": fraud from a public dataset, grouped by amount (the data doesn't say who committed it)
export type AttackerSource = "spawned" | "outsider" | "threat_intel" | "held_out" | "dataset";

// ShelfKey.id() in contracts/schemas.py: "<style>/<amount>/<side>"
export type ShelfId = string;

export interface Replay {
  run_id: string;
  seed: number;
  defense: string; // runs.defense
  defenders: DefenderInfo[]; // contracts/config.py MODEL_ROLES
  rounds: RoundReplay[];
  dataset?: DatasetInfo; // set when the run replays a public dataset instead of the simulated marketplace
}

// engine/public_replay.py: where the payments came from. Amounts are in the dataset's currency.
export interface DatasetInfo {
  name: string;
  source: string;
  url: string;
  license: string;
  currency: string; // ISO code, e.g. "EUR"
  round_hours: number; // each round is this many hours of the dataset, in time order
  start: string | null; // ISO time where round 1 starts, when the dataset has real dates (ULB only counts seconds)
}

export interface DefenderInfo {
  lane: Lane;
  model: string;
  question: string;
}

export interface RoundReplay {
  round: number;
  metrics: RoundMetrics; // round_metrics.payload
  // attackers.payload for every attacker that ran this round
  attackers: AttackerInfo[];
  // defender_actions, decided at the end of the previous round, played at the start of this one
  actions: DefenderAction[];
  // threat_reports that produced attackers this round
  news: NewsDrop[];
  waves: Wave[];
  // archive_snapshots after this round's update
  shelves: ShelfState[];
  // rulebook_versions after this round's reflection
  rulebooks: Rulebook[];
}

export interface RoundMetrics {
  loss_merchant_side_cents: number;
  loss_buyer_side_cents: number;
  false_positive_rate: number;
  exploitability_cents: number;
  shelves_defended: number;
  archive_coverage: number;
  frozen: boolean;
}

export interface AttackerInfo {
  attacker_id: string;
  name: string;
  source: AttackerSource;
  model: string | null;
  parent: Actor | null; // who built it, for spawned attackers
  lineage: string | null; // outsider persona name
  generation: number;
  shelf: ShelfId;
  strategy: string; // Playbook.strategy
  params: Record<string, number>; // Playbook.params at the start of the round
  profit_cents: number;
}

export interface DefenderAction {
  actor: Actor;
  action: "spawn" | "request_feature" | "request_scan" | "skip";
  granted: boolean;
  reason: string;
  shelf?: ShelfId; // detail.target_shelf for spawns
  attacker_id?: string; // the attacker the spawn produced
}

export interface NewsDrop {
  report_id: string;
  title: string;
  shelf: ShelfId;
  attacker_ids: string[];
}

export interface Wave {
  wave: number;
  // transactions + decisions + ground_truth + votes, one row per payment
  payments: Payment[];
  // playbook_waves rows written after this wave
  retunes: Retune[];
}

export interface Payment {
  tx_id: string;
  source: "legit" | "attacker";
  attacker_id: string | null;
  amount_cents: number;
  action: Action;
  settled: boolean;
  is_fraud: boolean;
  objected_by: Lane[]; // lanes whose vote was step_up or block
  escalated: boolean;
  explanation: string;
  breach?: boolean; // an honest payment over its human's mandate: blocking it is right, not a lost sale
}

export interface Retune {
  attacker_id: string;
  strategy_changed: boolean;
  trigger: string;
  params: Record<string, number>;
}

export interface ShelfState {
  shelf: ShelfId;
  attacker_ids: string[];
  outsider_slot: string | null;
  attempts: number;
  caught: number;
  unknown: boolean;
}

export interface Rulebook {
  lane: Lane;
  rules: string[];
  watch_rules: string[];
  added: number;
  retired: number;
}

// public/baseline.json: another defense's real decisions on the same payments, for the demo's
// step 0 ("without TagTeam"). Exported by the engine; see arena/README.md.
export interface Baseline {
  run_id: string;
  contender: string;
  label: string;
  trained_on: string;
  // A payment missing here was allowed and settled (real-data exports leave those out to stay small).
  decisions: Record<string, { action: Action; settled: boolean }>;
  pretrained?: boolean; // fitted on a training period before round 1, so it decides round 1 trained
  totals: BaselineTotals;
  ours_totals: BaselineTotals;
  // true when the baseline ran live against the same attackers (its own payments, public/paysim/xgboost.json)
  // rather than deciding TagTeam's recorded payments
  live?: boolean;
}

export interface BaselineTotals {
  total_cost_cents: number;
  fraud_cost_cents: number;
  honest_cost_cents: number;
  mean_fp: number;
  max_fp: number;
}
