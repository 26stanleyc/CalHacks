import path from "node:path";

import { expect, test } from "@playwright/test";
import { Client } from "pg";

import { dollars, pct } from "../src/lib/format";

// M5 smoke test (spec 8.2) against the seeded (or replayed) database and the engine API.
const ENGINE_URL = `http://localhost:${process.env.ENGINE_PORT ?? 8000}`;

if (!process.env.DATABASE_URL) {
  process.loadEnvFile(path.resolve(__dirname, "..", "..", ".env"));
}

async function db<T>(sql: string, params: unknown[] = []): Promise<T[]> {
  const client = new Client({ connectionString: process.env.DATABASE_URL, ssl: { rejectUnauthorized: false } });
  await client.connect();
  try {
    return (await client.query(sql, params)).rows as T[];
  } finally {
    await client.end();
  }
}

async function engineMetrics() {
  const response = await fetch(`${ENGINE_URL}/metrics`);
  expect(response.ok).toBeTruthy();
  return response.json();
}

test("the transaction stream shows rows with decisions", async ({ page }) => {
  await page.goto("/stream");
  const rows = page.getByTestId("tx-row");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThan(10);
  await expect(page.getByTestId("tx-action").first()).toHaveText(/allow|step-up|block/);
});

test("each curve has one point per round", async ({ page }) => {
  await page.goto("/");
  const runId = await page.locator('[data-testid="run-picker"] [data-current="true"]').innerText();
  const current = runId.split(" ")[0];
  const [{ rounds }] = await db<{ rounds: number }>(
    "select count(*)::int as rounds from round_metrics where run_id = $1", [current]);
  expect(rounds).toBeGreaterThan(1);
  for (const [id, series] of [["loss", 2], ["rates", 2], ["exploitability", 1]] as const) {
    const curve = page.getByTestId(`curve-${id}`);
    await expect(curve).toHaveAttribute("data-points", String(rounds));
    await expect(curve.locator(".recharts-line-dots circle, .recharts-line-dots .recharts-dot")).toHaveCount(rounds * series);
    await expect(page.getByTestId(`curve-table-${id}`).locator("tbody tr")).toHaveCount(rounds);
  }
  await expect(page.getByTestId("arena-grid")).toHaveAttribute("data-shelves", "24");
});

test("the freeze button changes the freeze state through the API", async ({ page }) => {
  const before = (await engineMetrics()).frozen as boolean;
  await page.goto("/");
  const badge = page.getByTestId("engine-frozen");
  await expect(badge).toHaveAttribute("data-frozen", String(before));

  await page.getByTestId("freeze-button").click();
  await expect(badge).toHaveAttribute("data-frozen", String(!before));
  expect((await engineMetrics()).frozen).toBe(!before);

  await page.getByTestId("freeze-button").click(); // put it back
  await expect(badge).toHaveAttribute("data-frozen", String(before));
  expect((await engineMetrics()).frozen).toBe(before);
});

test("numbers shown match GET /metrics", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("engine-panel")).toBeVisible();
  const m = await engineMetrics();
  await expect(page.getByTestId("engine-run")).toHaveText(m.run_id);
  await expect(page.getByTestId("engine-round")).toHaveText(String(m.round));
  await expect(page.getByTestId("engine-loss-merchant")).toHaveText(dollars(m.loss_merchant_side_cents));
  await expect(page.getByTestId("engine-loss-buyer")).toHaveText(dollars(m.loss_buyer_side_cents));
  await expect(page.getByTestId("engine-fpr")).toHaveText(pct(m.false_positive_rate));
  await expect(page.getByTestId("engine-escalation")).toHaveText(pct(m.escalation_rate));
  await expect(page.getByTestId("engine-exploitability")).toHaveText(dollars(m.exploitability_cents));
  await expect(page.getByTestId("engine-shelves-defended")).toHaveText(pct(m.shelves_defended, 0));
  await expect(page.getByTestId("engine-archive-coverage")).toHaveText(pct(m.archive_coverage, 0));
});

test("the curves show the logged round metrics", async ({ page }) => {
  await page.goto("/");
  const current = (await page.locator('[data-testid="run-picker"] [data-current="true"]').innerText()).split(" ")[0];
  const rows = await db<{ round: number; payload: { loss_merchant_side_cents: number; exploitability_cents: number } }>(
    "select round, payload from round_metrics where run_id = $1 order by round", [current]);
  const table = page.getByTestId("curve-table-loss");
  for (const r of rows) {
    await expect(table.locator(`tr[data-round="${r.round}"] td`).nth(1)).toHaveText(dollars(r.payload.loss_merchant_side_cents));
  }
});

test("run round advances the engine's round", async ({ page }) => {
  const before = (await engineMetrics()).round as number;
  await page.goto("/");
  await page.getByTestId("run-round-button").click();
  await expect(page.getByTestId("engine-round")).toHaveText(String(before + 1));
  expect((await engineMetrics()).round).toBe(before + 1);
});
