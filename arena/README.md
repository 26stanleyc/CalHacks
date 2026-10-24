# Arena

A 3D replay of a training run as a little battle on a hex island. Five angel specialists and the orchestrator guard the x402 gate in the middle. Devil attackers stand on the 24 arena shelves around the edge and throw payments (coins) at the gate. Angels zap the coins they catch, and stolen coins drop into the vault.

```bash
npm install
npm run dev   # http://localhost:5174
```

## What's on screen

| In the world | In the system |
|---|---|
| Centre tile with the gold arch | The x402 facilitator, where every payment is decided |
| Inner ring of angels | The 5 specialists, plus the orchestrator (bigger, holding a gold ring) |
| Outer ring of 24 raised tiles | The arena shelves: attacks on stores on one half, attacks on shoppers on the other |
| Shelf colour | Catch rate over the last 2 rounds: red under 60%, amber under 80%, green at 80% or more, sand for too few attempts to trust, grey for empty |
| Devil's foot ring | Blue: built by one of our angels (insider). Black with a top hat: outsider persona. Orange with a paper hat: from a news report |
| Grey blobs | Honest shoppers. Their coins are gold; attack coins are crimson |
| Blue beam at round start | A defender building an attacker aimed at that shelf |
| Newspaper falling | A threat-intel report turning into new attackers |
| "!" bubble over a devil | It got caught a lot and is rethinking its plan between waves; dots mean it's only re-tuning its numbers |
| Gold ring rising off an angel | It added a rule while learning |

Each round plays in 27 s at 1×: 3 s of spawning, three 7 s waves, then 3 s of learning. The timeline bars are the arms-race chart: each bar's height is that round's exploitability. Click a bar to jump to that round. Click any creature, shelf or the gate to open its card.

Keys: space plays and pauses, the left and right arrows step rounds, Esc closes the card.

## Data

The app plays `public/replay.json` if it exists, and otherwise a deterministic mock run (`src/data/mock.ts`). The format is `src/data/types.ts`. Every field comes from a table in `contracts/db.sql`, so an exporter needs one query per table per round:

| Replay field | Source |
|---|---|
| `rounds[].metrics` | `round_metrics.payload` |
| `rounds[].attackers` | `attackers.payload` for attackers that ran that round |
| `rounds[].actions` | `defender_actions` from the end of the previous round (`detail.target_shelf`, plus the spawned attacker id) |
| `rounds[].news` | `threat_reports` whose attackers first ran that round |
| `rounds[].waves[].payments` | `transactions` joined with `decisions`, `ground_truth` and `votes` (`objected_by` = lanes that voted step_up or block) |
| `rounds[].waves[].retunes` | `playbook_waves` |
| `rounds[].shelves` | `archive_snapshots` after the round |
| `rounds[].rulebooks` | `rulebook_versions` after reflection |

Only a sample of attack coins is drawn (at most 40 per wave) so the sky stays readable. The scoreboard counts every payment.

In dev builds, `window.arena` is the playback store, e.g. `arena.setState({ round: 7, t: 12, playing: false })`.
