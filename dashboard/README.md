# Dashboard (R3)

Next.js 16 (App Router, Cache Components) dashboard for the fraud defense. It reads the Postgres log tables (`contracts/db.sql`) server-side, and it calls the engine only through the HTTP bridge in `contracts/api.py`.

- **Overview** (`/`): live engine panel (`GET /metrics`, Freeze and Run round buttons), per-round curves (loss by side, false-positive and escalation rate, exploitability, with the frozen stretch shaded), and the 24-shelf arena grid.
- **Transaction stream** (`/stream`): newest payments with their decision, escalation and injection flags. Click a row for its investigator case file.
- **Attacker drift** (`/attackers`): each outsider lineage's playbook per wave, and its strategy rewrites.

Pick a run with `?run=<run_id>`. The newest run with round metrics is the default.

## Run

From the repo root, start the engine API (stub until Stanley's engine serves the same routes):

```bash
uv run uvicorn contracts.api:create_stub_app --factory --port 8000
```

Then the dashboard:

```bash
cd dashboard && npm install && npm run dev
```

It opens on http://localhost:3000.

- `DATABASE_URL` comes from the repo-root `.env`. It's read on the server only and never sent to the browser.
- `ENGINE_API_URL` defaults to `http://localhost:8000`.

## Checks

```bash
npm run lint && npm run typecheck && npm run build
npx playwright install chromium   # once
npm run test:e2e                  # starts the stub API (ENGINE_PORT, default 8000) and the dashboard on :3100
```
