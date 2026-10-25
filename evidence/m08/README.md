# M8: ASI:One demo runbook (Fetch.ai track)

The public agents, **fraud-desk** and **merchant-scanner**, are uAgents with the Chat Protocol
and an Agentverse mailbox. By default they call the real engine served by `engine.serve`
(`ENGINE_SOURCE=http`). That is the same engine the dashboard uses, so chat and dashboard
always agree. The canned `StubEngine` is used only when `ENGINE_SOURCE=stub` is set explicitly.

| agent | address |
|---|---|
| fraud-desk | `agent1q2hr8875u86ac49yd8tf7ehg5cygl2y7vzdaxse8jq0fxlwqevm0zfnz9m7` |
| merchant-scanner | `agent1qd73ye5agcd0xleqkg6u75nsc0khr74znjeynl8x5ywedaux84ytu49snwx` |

Agentverse profiles:
- <https://agentverse.ai/agents/details/agent1q2hr8875u86ac49yd8tf7ehg5cygl2y7vzdaxse8jq0fxlwqevm0zfnz9m7/profile>
- <https://agentverse.ai/agents/details/agent1qd73ye5agcd0xleqkg6u75nsc0khr74znjeynl8x5ywedaux84ytu49snwx/profile>

The addresses come from `FRAUD_DESK_AGENT_SEED` and `MERCHANT_SCANNER_AGENT_SEED` in `.env`, so they
are the same on every machine that has that `.env`.

## 1. Start everything (one command)

```bash
uv run python -m integrations.asi_one.demo
```

This command:
- starts `engine.serve` on :8000, or reuses one that is already running;
- starts both agents with `ENGINE_SOURCE=http`;
- waits until both mailboxes say "Mailbox linked";
- prints each agent's `agent1q…` address, its local inspector link and its Agentverse profile link.

Ctrl-C stops everything it started. Logs go to `integrations/.local/logs/` (gitignored).

To print only the addresses and links:

```bash
uv run python -m integrations.asi_one.demo --addresses
```

Useful switches (in `.env` or on the command line):
- **`ENGINE_RUN_ID=<run>`** resumes an existing run from the database, so "how is the defense doing?"
  reports its rounds. Without it, `engine.serve` starts a fresh `dev-…` run at round 0.
- **`ENGINE_STORE=memory`** keeps everything off the shared database (what the verification below used).
- **Start `engine.serve` yourself first** if you want different settings for it; the demo command
  reuses whatever is already answering at `ENGINE_HTTP_URL`.

Before chatting, run at least one round (the dashboard's Run round button, or the command below),
so the metrics are not all zero:

```bash
curl -X POST -H 'X-Requested-By: dashboard' http://127.0.0.1:8000/run_round
```

## 2. Allow ourselves to "run a round" from chat (one place: `TEAM_ALLOWLIST_ADDRESSES`)

Round controls ("run a round", "freeze learning") are refused for every sender that is not in
`TEAM_ALLOWLIST_ADDRESSES` in `.env`. The variable is **empty today, so they are refused for us too.**

1. Chat "run a round" to fraud-desk from ASI:One. You will get the team-only refusal.
2. The Fraud Desk log (`integrations/.local/logs/fraud-desk.log`) prints the refused sender's full
   address on its own line:
   ```text
   WARNING:integrations.asi_one.router:Refused run_round: sender is not in TEAM_ALLOWLIST_ADDRESSES. To allow it, add this address to TEAM_ALLOWLIST_ADDRESSES in .env and restart the engine and agents:
   agent1q…
   ```
3. Paste that address into `.env` (comma-separated for more than one person):
   `TEAM_ALLOWLIST_ADDRESSES=agent1q…,agent1q…`
4. Restart the demo command. **Both** the agent and `engine.serve` check the list, and each reads it
   at startup. If only the agent was restarted, the log says "Restart engine.serve" instead.

Everyone else is still refused. Tests: `integrations/tests/test_asi_one.py`, the
"allowlist lives in one place" section, and `test_m08_real_engine.py`, section 3.

## 3. The ASI:One steps (manual, for the recording)

1. Open <https://asi1.ai> and sign in with the account that owns `AGENTVERSE_API_KEY`.
2. Start a **fresh** chat. Search for `fraud-desk`, or paste its address from the table above.
3. Send the three M8 prompts:
   1. `Check this payment: agent a-1 paying merchant m-7 $49 for a USB-C charger, mandate under $30`
      → **Blocked**, hard veto `mandate:over_cap`, with a tx id like `asi-…`
   2. `Why was asi-… blocked?`, using the tx id from the reply above. (`tx-1234` from the spec has
      no record, so it only produces "I have no record".)
   3. `How is the defense doing?` → the engine's run id, round and both loss lines
4. Optional:
   - `Run a round`, which is refused unless your address is allowlisted (§2).
   - Open merchant-scanner and ask:
     `Is https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html safe for my shopping agent?`

## 4. Still needs a human

Claude cannot do these steps. Check each one off when it's done:

- [ ] **Screenshot** of the hello agent replying in ASI:One → `evidence/m0/asi-one-hello.png`
      (`uv run python -m integrations.asi_one.hello_agent`, then follow `evidence/m0/manual-checks.md` §1).
- [ ] **Screen recording** of §3 (the three prompts, from a fresh ASI:One session) → `evidence/m08/`
      (e.g. `evidence/m08/asi-one-fraud-desk.mov`).
- [ ] **Our ASI:One sender addresses** → `TEAM_ALLOWLIST_ADDRESSES` in `.env` (§2). This is needed
      to show "run a round" or "freeze learning" live.
- [ ] **Cal Hacks hackpack:** check the Fetch.ai track's submission requirements, such as Agentverse
      profile links, the ASI:One chat and any required README badge or tags, and the rules on custom
      frontends.

## 5. What was verified automatically

**Over the real mailbox, on the real engine:** [`mailbox-transcript.md`](mailbox-transcript.md)
(5/5 replies as expected).

The command was `uv run python -m integrations.asi_one.mailbox_check`. A separate uAgent
(`agent1qg2kazrf…`, not allowlisted) sent each prompt through Agentverse to the running agents and
back, just as ASI:One does. Setup:

- the demo command;
- `engine.serve` started first with `ENGINE_STORE=memory USE_MOCK_LLM=1 ENGINE_RUN_ID=m08-mailbox-check`,
  so there were no shared-database writes and no model spend for the round;
- one round run beforehand;
- the agents' routing model was Bitdeer through `make_llm()`, with keyword routing as the fallback.

Results:
1. The payment check was **Blocked** with the `mandate:over_cap` veto.
2. Explain returned the stored decision for that same tx.
3. The metrics came from run `m08-mailbox-check`, round 1.
4. "Run a round" was refused.
5. The Merchant Scanner did a live Apify scan of a real URL.

**M8 automated checks** (`uv run pytest integrations/tests`):
- `test_m08_real_engine.py` runs against the real `FraudEngine`, both in-process and over the HTTP
  bridge:
  1. A chat payment gets the same decision as calling `check_payment` directly.
  2. Chat payments never get a label, never reach the learning loop and never appear in
     `reflections`, `llm_spend`, `archive_snapshots` or `archive_events` (the "query returns 0" check).
     They also change nothing in a round's cost or false positives.
  3. A stranger asking to run a round or freeze is refused. `engine.serve` refuses it even if the chat
     layer's own list were wrong.
- `test_asi_one.py` covers the chat layer's own rules:
  - the `source=asi_one` tag can't be overridden;
  - the allowlist is read from `.env`, and refusals log the sender's address;
  - the stub is used only when asked for by name.
