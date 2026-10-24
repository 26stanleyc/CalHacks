"use server";

import { refresh } from "next/cache";

import * as engine from "@/lib/engine";

// Server Functions are reachable by direct POST. That's acceptable here because the dashboard runs
// on localhost only, like the engine API's trusted X-Requested-By header (contracts/README.md).

export async function toggleFreeze(formData: FormData): Promise<void> {
  await engine.setFreeze(formData.get("on") === "true");
  refresh();
}

export async function runRound(): Promise<void> {
  await engine.runRound();
  refresh();
}
