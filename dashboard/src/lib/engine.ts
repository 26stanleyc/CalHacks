import "server-only";

import type { RoundMetrics } from "@/lib/types";

// The engine's HTTP bridge (contracts/api.py). The stub engine serves it today; Stanley's real
// engine serves the same routes at I1, so nothing here changes then.
export const ENGINE_URL = process.env.ENGINE_API_URL ?? "http://localhost:8000";
const REQUESTED_BY = { "X-Requested-By": "dashboard" }; // always allowed for team-only routes

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${ENGINE_URL}${path}`, { cache: "no-store", ...init });
  if (!response.ok) {
    throw new Error(`engine ${path}: HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function engineMetrics(): Promise<RoundMetrics | null> {
  try {
    return await call<RoundMetrics>("/metrics");
  } catch {
    return null; // engine not running: the panel says so instead of failing the page
  }
}

export function setFreeze(on: boolean): Promise<{ frozen: boolean }> {
  return call("/freeze", {
    method: "POST",
    headers: { ...REQUESTED_BY, "Content-Type": "application/json" },
    body: JSON.stringify({ on }),
  });
}

export function runRound(): Promise<RoundMetrics> {
  return call("/run_round", { method: "POST", headers: REQUESTED_BY });
}
