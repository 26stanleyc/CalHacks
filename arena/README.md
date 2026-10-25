# TagTeam site and arena

Four pages, one Vite app (`/ethereum/` is the arena on real data and `/phishing/` a payment log of one real scam; see Real data below):

- **`/`, the overview:** a hero (a payment slip telling one hijacked purchase in order, then stamped by the five specialists) and four chapters: why it matters, how it works, what it saves, built on. Opening a chapter wipes it in while its title morphs out of its tile; next/previous (or the arrow keys) swipe; Escape or back wipes home. Each chapter has its own `#hash`. Every figure is sourced. Industry numbers link to their articles. The results come from `evidence/head-to-head/bitdeer-stacking.md` on `stanley/core-engine`: real Bitdeer models, seeds 100–102, 6 rounds each.
- **`/demo/`, the live arena:** a 3D replay of a training run as a battle on a hex island. Five angel specialists and the orchestrator guard the x402 gate in the middle. Devil attackers stand on the 24 arena shelves around the edge and throw payments (coins) at the gate. Angels zap the coins they catch, and stolen coins drop into the vault.

```bash
npm install
npm run dev   # http://localhost:5174 (overview) and http://localhost:5174/demo/ (arena)
```

Moving between the pages uses the browser's cross-page View Transitions: the page cross-fades and the TagTeam header glides into place. Hovering a page link prerenders the other page, so the switch is near-instant. For the header to glide, it and the stylesheets have to be on each page's first frame. That's why the headers are static HTML in `index.html` and `demo/index.html`, and the CSS is linked there rather than imported from JS. Edit the header in both files.

The overview's look borrows from security printing, the engraved line work on banknotes that exists to stop forgery: a banknote-green ground, paper-coloured ink, blue for the defense and red for fraud. The arena keeps its daylight island so creatures read on a projector, with its panels in the same green.

## What's on screen

| In the world | In the system |
|---|---|
| Centre tile with the gold arch | The x402 facilitator, where every payment is decided |
| Inner ring of angels | The 5 specialists, plus the orchestrator (bigger, holding a gold ring) |
| Outer ring of 24 raised tiles | The arena shelves: attacks on stores on one half, attacks on shoppers on the other |
| Shelf colour | Catch rate over the last 3 rounds: red under 60%, amber under 80%, green at 80% or more, sand for too few attempts to trust, grey for empty |
| Devil's foot ring | Blue: built by one of our angels (insider). Black with a top hat: outsider persona. Orange with a paper hat: from a news report |
| Grey blobs | Honest shoppers. Their coins are gold; attack coins are crimson |
| Blue beam at round start | A defender building an attacker aimed at that shelf |
| Newspaper falling | A threat-intel report turning into new attackers |
| "!" bubble over a devil | It got caught a lot and is rethinking its plan between waves; dots mean it's only re-tuning its numbers |
| Gold ring rising off an angel | It added a rule while learning |

The panel at the top right is the same 24 shelves flattened into a grid, the figure from the Rainbow Teaming paper. Rows are attack styles; columns are amount bands, split into attacks on stores and attacks on shoppers. Cells use the tile colours and recolour with them during learning. A gold halo marks each shelf a defender built an attacker for this round; an orange one marks a news scam. Click a cell to open that shelf. The panel is hidden on phones.

Each round plays in 27 s at 1×: 3 s of spawning, three 7 s waves, then 3 s of learning. The timeline bars are the arms-race chart: each bar's height is that round's exploitability. Click a bar to jump to that round. Click any creature, shelf or the gate to open its card.

Keys: space plays and pauses, the left and right arrows step rounds, Esc closes the card.

## Data

The app plays `public/replay.json` if it exists, and otherwise a deterministic mock run (`src/data/mock.ts`). The checked-in replay is a recorded run on real Bitdeer models: `h2h-real-42-202610241623`, 4 rounds, 2,056 payments. To re-export a run, go to the `stanley-core-engine` worktree and run:

```bash
uv run python -m engine.export_replay <run_id> --out ../arena-viz/arena/public/replay.json
```

**Step 0 (without TagTeam).** If `public/baseline.json` exists for the same `run_id`, the demo opens with a baseline defense deciding the same recorded payments: XGBoost trained once, on round 1's fraud, then frozen. Round 1 is decided untrained, and the screen says so. When its rounds end, TagTeam deploys on its own and the run continues on one timeline. Scoreboard, timeline and closing note compare the two by net: honest payments through minus money stolen (higher is better). Re-export both files together whenever the run changes, or step 0 disappears:

```bash
uv run python -m engine.export_baseline <run_id> --out ../arena-viz/arena/public/baseline.json
``` The format is `src/data/types.ts`. Every field comes from a table in `contracts/db.sql`, so an exporter needs one query per table per round:

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

### Real data (second data set)

The same run also has its own page, **`/ethereum/`** (nav: Real data): the arena locked to it, step 0 first, with the data's source in place of the switch. On `/demo/`, a switch at the top right picks the data set: **Simulated marketplace** (the default, everything above) or **Real Ethereum phishing**: real Ethereum transactions around 1,165 addresses Etherscan labels as phishing, from XBlock (Sun Yat-sen University; Chen et al., ACM TOIT 2021; [xblock.pro](https://xblock.pro/#/dataset/13), Kaggle `xblock/ethereum-phishing-transaction-network`; no license stated). The choice is kept in the URL (`/demo/?data=eth`). The switch only lists data sets whose files exist (`public/eth/`, and `public/real/` if the ULB card data is ever exported), and it's hidden on phones.

`public/eth/replay.json.gz` and `public/eth/baseline.json` are the same formats as above, made by `engine/public_replay.py` on the `stanley/real-data-engine` branch. The test week is Mar 5-10, 2018 (49,389 payments, 979 into phishing addresses), one day per round. Both defenses first train on the 7 labelled days before it: XGBoost is fitted once and frozen, TagTeam learns a day at a time (real Bitdeer models write its rules) and keeps learning during the week. Both also get a "receiver memory" feature: how many labelled phishing payments the receiver got before, like a blocklist of reported scam addresses. XGBoost's copy stops at its training days; TagTeam's grows every night. Step 0 works as it does for the simulated run. Result: TagTeam stops 634 of 979 phishing payments against 417 (Ξ364 lost against Ξ628), with 5 false alarms against 0; net Ξ465,896 against Ξ465,636. The memory was chosen on a separate validation week before it touched this one; `evidence/public-data/ethereum-arena.md` on the engine branch has the field mapping, what's approximated, and every setup tried, including the ones where TagTeam lost.

The replay is gzipped because it holds every payment (the scoreboard counts them all); the app unzips it in the browser. The baseline file lists only decisions that aren't "allow, settled", which is what the app assumes for a missing payment. To re-export, see the commands in that evidence file.

What differs on screen: amounts are in ether (Ξ); the data has no attack styles, so phishing payments stand on the beach in three groups by amount (teal ring) instead of on the shelves, and the shelf grid is hidden; phase lines give the real UTC dates each wave covers and don't say attackers adapt (recorded fraud doesn't).

### Phishing log (`/phishing/`)

One real scam followed payment by payment: the phishing address TagTeam stopped the most payments into during the test week (`0xd020…84cb`, 117 victim payments from Mar 6, 2018), each with the 3 ordinary payments just before it, scrolling as a log (time, from, to, amount) with TagTeam's and XGBoost's decisions. Between days a row shows the night's learning: the scam's confirmed reports reaching TagTeam's memory while XGBoost's stays at what it had before the week. `public/eth/log.json` comes from `engine/phishing_log.py` on the engine branch, from the same run as `public/eth/`; nothing is re-decided. Space plays and pauses.

In dev builds, `window.arena` is the playback store, e.g. `arena.setState({ round: 7, t: 12, playing: false })`.
