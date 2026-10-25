import "server-only";

import path from "node:path";

import { Pool } from "pg";

// The repo-root .env holds DATABASE_URL (shared with the Python side). It is read here, on the
// server only, and never exposed to the browser: nothing in it is prefixed NEXT_PUBLIC_.
// loadEnvFile never overrides variables that are already set.
if (!process.env.DATABASE_URL) {
  try {
    process.loadEnvFile(path.resolve(process.cwd(), "..", ".env"));
  } catch {
    // no root .env: makePool reports the missing variable
  }
}

const globalForPool = globalThis as unknown as { dashboardPool?: Pool };

function makePool(): Pool {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    throw new Error("DATABASE_URL is not set (add it to the repo-root .env).");
  }
  const local = /@(localhost|127\.0\.0\.1)[:/]/.test(connectionString);
  return new Pool({
    connectionString,
    max: 4,
    // Supabase serves TLS with its own CA, which Node doesn't trust by default.
    ssl: local ? undefined : { rejectUnauthorized: false },
  });
}

export function pool(): Pool {
  globalForPool.dashboardPool ??= makePool();
  return globalForPool.dashboardPool;
}

export async function query<T>(text: string, params: unknown[] = []): Promise<T[]> {
  const result = await pool().query(text, params);
  return result.rows as T[];
}
