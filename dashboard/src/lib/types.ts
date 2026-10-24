// Mirrors of the contracts/schemas.py models the dashboard reads (JSONB payloads).

export type Action = "allow" | "step_up" | "block";

export interface RoundMetrics {
  run_id: string;
  seed: number;
  round: number;
  defense: string;
  loss_merchant_side_cents: number;
  loss_buyer_side_cents: number;
  false_positive_rate: number;
  escalation_rate: number;
  uncovered_misses: number;
  exploitability_cents: number;
  hijack_rate: number;
  shelves_defended: number;
  archive_coverage: number;
  total_attacker_profit_cents: number;
  first_contact_catch_rate: number | null;
  critic_agreement: number | null;
  llm_spend_usd: number;
  frozen: boolean;
}

export interface ToolCall {
  tool: string;
  input: Record<string, unknown>;
  output_summary: string;
}

export interface ShelfKey {
  style: "burst" | "slow_drip" | "many_accounts" | "hijack_injection";
  amount: "under_10" | "10_to_100" | "over_100";
  side: "merchant" | "buyer";
}

export interface ShelfSnapshot {
  key: ShelfKey;
  attacker_ids: string[];
  outsider_slot: string | null;
  attempts: number;
  caught: number;
  unknown: boolean;
}
