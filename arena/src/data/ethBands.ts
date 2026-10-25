// The Ethereum run groups its phishing payments into the engine's three amount bands, and nearly all of
// them are under Ξ10, so they all came from one spot on the beach. This splits them into finer bands,
// each its own group standing at its own spot around the island, so attacks come from every side.
// Only the grouping changes: every payment and decision stays as recorded.

import type { AttackerInfo, Replay, ShelfId } from "./types";

// Band edges in hundredths of ether (the replay's amount_cents): under Ξ0.02, Ξ0.02 to Ξ0.05, ..., over Ξ5.
export const ETH_BAND_EDGES = [2, 5, 10, 20, 30, 40, 50, 75, 100, 200, 500];
export const ETH_BAND_COUNT = ETH_BAND_EDGES.length + 1;

const bandOf = (cents: number) => {
  const i = ETH_BAND_EDGES.findIndex((e) => cents < e);
  return i < 0 ? ETH_BAND_EDGES.length : i;
};
// "dataset/eth<k>/buyer": layout.ts and words.ts read k back.
const shelfOf = (band: number): ShelfId => `dataset/eth${band}/buyer`;
const idOf = (band: number) => `dataset-fraud-eth${band}`;

export function splitEthBands(replay: Replay) {
  for (const r of replay.rounds) {
    const template = r.attackers.find((a) => a.source === "dataset");
    if (!template) continue;
    const used = new Set<number>();
    for (const w of r.waves)
      for (const p of w.payments) {
        if (p.source !== "attacker" || !p.attacker_id?.startsWith("dataset-")) continue;
        const band = bandOf(p.amount_cents);
        p.attacker_id = idOf(band);
        used.add(band);
      }
    const bands = [...used].sort((a, b) => a - b);
    const attackers: AttackerInfo[] = bands.map((band) => ({
      ...template,
      attacker_id: idOf(band),
      name: `Payments to phishing addresses, ${ethBandLabel(band)}`,
      shelf: shelfOf(band),
      strategy: `Every payment of ${ethBandLabel(band)} into an address Etherscan labels as phishing: a victim paying a scammer. Grouped by amount; the scammers themselves aren't drawn one by one.`,
    }));
    r.attackers = [...r.attackers.filter((a) => a.source !== "dataset"), ...attackers];
    r.shelves = [
      ...r.shelves.filter((s) => !s.shelf.startsWith("dataset/")),
      ...bands.map((band) => ({
        shelf: shelfOf(band), attacker_ids: [idOf(band)], outsider_slot: null, attempts: 0, caught: 0, unknown: true,
      })),
    ];
  }
}

const eth = (cents: number) => `Ξ${cents / 100}`;
export function ethBandLabel(band: number): string {
  if (band === 0) return `under ${eth(ETH_BAND_EDGES[0])}`;
  if (band === ETH_BAND_EDGES.length) return `over ${eth(ETH_BAND_EDGES[band - 1])}`;
  return `${eth(ETH_BAND_EDGES[band - 1])} to ${eth(ETH_BAND_EDGES[band])}`;
}
