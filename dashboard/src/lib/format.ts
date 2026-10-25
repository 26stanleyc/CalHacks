export function dollars(cents: number): string {
  const d = cents / 100;
  return d >= 1000 ? `$${(d / 1000).toFixed(1)}k` : `$${d.toFixed(d < 10 ? 2 : 0)}`;
}

export function pct(x: number | null | undefined, digits = 1): string {
  return x == null ? "—" : `${(x * 100).toFixed(digits)}%`;
}

/** Contiguous [start, end] round ranges where `flag` holds, e.g. the frozen stretch. */
export function ranges<T extends { round: number }>(rows: T[], flag: (r: T) => boolean): [number, number][] {
  const out: [number, number][] = [];
  for (const r of rows) {
    if (!flag(r)) continue;
    const last = out[out.length - 1];
    if (last && last[1] === r.round - 1) last[1] = r.round;
    else out.push([r.round, r.round]);
  }
  return out;
}
