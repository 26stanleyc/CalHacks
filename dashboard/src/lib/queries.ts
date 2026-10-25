import "server-only";

import { connection } from "next/server";

import { query } from "@/lib/db";
import type { Action, RoundMetrics, ShelfSnapshot, ToolCall } from "@/lib/types";

// Every reader waits for a request (connection()), so nothing from the database is prerendered.

export interface RunRow {
  run_id: string;
  seed: number;
  defense: string;
  rounds: number;
}

/** Runs that have logged round metrics, newest first. */
export async function listRuns(): Promise<RunRow[]> {
  await connection();
  return query<RunRow>(
    `select r.run_id, r.seed, r.defense, count(m.round)::int as rounds
       from runs r join round_metrics m using (run_id)
      group by r.run_id, r.seed, r.defense, r.started_at
      order by r.started_at desc`,
  );
}

/** The requested run if it exists, else the newest run with metrics. */
export async function resolveRun(requested?: string): Promise<RunRow | null> {
  const runs = await listRuns();
  return runs.find((r) => r.run_id === requested) ?? runs[0] ?? null;
}

export async function roundMetrics(runId: string): Promise<RoundMetrics[]> {
  await connection();
  const rows = await query<{ payload: RoundMetrics }>(
    "select payload from round_metrics where run_id = $1 order by round",
    [runId],
  );
  return rows.map((r) => r.payload);
}

export interface StreamRow {
  tx_id: string;
  round: number;
  wave: number;
  source: string;
  attacker_id: string | null;
  agent_id: string;
  merchant_id: string;
  amount_cents: number;
  memo: string;
  action: Action | null;
  escalated: boolean;
  weighted_score: number | null;
  vetoed_by: string | null;
  explanation: string | null;
  is_fraud: boolean | null;
  injection_score: number | null;
  tool_calls: ToolCall[] | null;
  investigator_decision: Action | null;
  investigator_reasons: string | null;
}

export async function transactionStream(runId: string, round: number | null, limit = 100): Promise<StreamRow[]> {
  await connection();
  return query<StreamRow>(
    `select t.tx_id, t.round, t.wave, t.source, t.attacker_id,
            t.payload->>'agent_id' as agent_id, t.payload->>'merchant_id' as merchant_id,
            (t.payload->>'amount_cents')::int as amount_cents, coalesce(t.payload->>'memo', '') as memo,
            d.action, coalesce(d.escalated, false) as escalated,
            (d.payload->>'weighted_score')::float as weighted_score, d.payload->>'vetoed_by' as vetoed_by,
            d.payload->>'explanation' as explanation, g.is_fraud,
            (f.feature_values->>'b_memo_injection')::float as injection_score,
            e.tool_calls, e.decision as investigator_decision, e.reasons as investigator_reasons
       from transactions t
       left join decisions d using (tx_id)
       left join ground_truth g using (tx_id)
       left join features f using (tx_id)
       left join escalations e using (tx_id)
      where t.run_id = $1 and ($2::int is null or t.round = $2)
      order by t.round desc, (t.payload->>'ts')::float desc
      limit $3`,
    [runId, round, limit],
  );
}

/** The latest round's shelves, with attempts and caught summed over the last 3 rounds (spec 3.9, point 2). */
export async function arenaSnapshot(runId: string): Promise<{ round: number; shelves: ShelfSnapshot[] } | null> {
  await connection();
  const rows = await query<{ round: number; payload: ShelfSnapshot; attempts: number; caught: number }>(
    `with latest as (select max(round) as r from archive_snapshots where run_id = $1),
          recent as (
            select shelf_id, sum((payload->>'attempts')::int)::int as attempts,
                   sum((payload->>'caught')::int)::int as caught
              from archive_snapshots, latest
             where run_id = $1 and round > latest.r - 3
             group by shelf_id)
     select s.round, s.payload, recent.attempts, recent.caught
       from archive_snapshots s join latest on s.round = latest.r join recent using (shelf_id)
      where s.run_id = $1
      order by s.shelf_id`,
    [runId],
  );
  return rows.length
    ? { round: rows[0].round, shelves: rows.map((r) => ({ ...r.payload, attempts: r.attempts, caught: r.caught })) }
    : null;
}

export interface DriftRow {
  attacker_id: string;
  name: string;
  round: number;
  wave: number;
  amount_cents: number | null;
  interval_s: number | null;
  strategy: string;
  strategy_changed: boolean;
  trigger: string | null;
}

/** Playbook per wave for the outsider lineages (the attackers that run every round). */
export async function outsiderDrift(runId: string): Promise<DriftRow[]> {
  await connection();
  return query<DriftRow>(
    `select w.attacker_id, coalesce(a.name, w.attacker_id) as name, w.round, w.wave,
            (w.playbook->'params'->>'amount_cents')::float as amount_cents,
            (w.playbook->'params'->>'interval_s')::float as interval_s,
            w.playbook->>'strategy' as strategy, w.strategy_changed, w.trigger
       from playbook_waves w
       join (select distinct on (attacker_id) attacker_id, payload->>'name' as name
               from attackers where run_id = $1 and source = 'outsider'
              order by attacker_id, round desc) a using (attacker_id)
      where w.run_id = $1
      order by w.attacker_id, w.round, w.wave`,
    [runId],
  );
}

export interface CrsRunRow {
  run_id: string;
  checks: number;
  live: number;
}

/** Runs whose decisions carry a CRS report (decisions.payload.crs), newest first. */
export async function listCrsRuns(): Promise<CrsRunRow[]> {
  await connection();
  return query<CrsRunRow>(
    `select t.run_id, count(*)::int as checks,
            count(*) filter (where d.payload->'crs'->>'mocked' = 'false')::int as live
       from decisions d join transactions t using (tx_id) join runs r using (run_id)
      where jsonb_typeof(d.payload->'crs') = 'object'
      group by t.run_id, r.started_at
      order by r.started_at desc`,
  );
}

export interface CrsCheckRow {
  tx_id: string;
  round: number;
  source: string;
  attacker_id: string | null;
  amount_cents: number;
  is_fraud: boolean | null;
  action: Action;
  score: number;
  band: [number, number];
  mocked: boolean;
  watch: string | null;
  hidden: string[];
  reasons: string;
  tool_calls: ToolCall[];
}

/** Every payment CRS checked in a run, latest first. */
export async function crsChecks(runId: string): Promise<CrsCheckRow[]> {
  await connection();
  return query<CrsCheckRow>(
    `select t.tx_id, t.round, t.source, t.attacker_id, (t.payload->>'amount_cents')::int as amount_cents,
            g.is_fraud, d.action, (c->>'score')::float as score, c->'band' as band, (c->>'mocked')::boolean as mocked,
            c->>'watch' as watch, coalesce(c->'hidden', '[]') as hidden, c->>'reasons' as reasons,
            coalesce(c->'tool_calls', '[]') as tool_calls
       from decisions d
       join transactions t using (tx_id)
       left join ground_truth g using (tx_id)
       cross join lateral (select d.payload->'crs' as c) x
      where t.run_id = $1 and jsonb_typeof(c) = 'object'
      order by t.round desc, (t.payload->>'ts')::float desc`,
    [runId],
  );
}
