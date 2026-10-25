"""Start the engine and both public agents for the ASI:One demo, in one command (M8).

    uv run python -m integrations.asi_one.demo              # engine.serve + Fraud Desk + Merchant Scanner
    uv run python -m integrations.asi_one.demo --addresses  # only print the addresses and links

Starts `engine.serve` (unless something already answers at ENGINE_HTTP_URL), waits for it,
then starts both agents with `ENGINE_SOURCE=http` so they share that one engine with the
dashboard. Prints each agent's `agent1q...` address and its Agentverse links, waits until
both mailboxes are linked, and stops everything on Ctrl-C. Logs go to
`integrations/.local/logs/` (gitignored). See `evidence/m08/README.md`.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from integrations.asi_one.engine_source import engine_reachable
from integrations.asi_one.mailbox_check import address_of
from integrations.settings import chat_allowlist, engine_http_url

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "integrations" / ".local" / "logs"
AGENTS = (("fraud-desk", "fraud_desk", 8102), ("merchant-scanner", "merchant_scanner", 8103))
ENGINE_START_TIMEOUT_S = 300
MAILBOX_TIMEOUT_S = 90


def print_addresses() -> None:
    for name, seed_name, port in AGENTS:
        address = address_of(seed_name)
        print(f"\n{name}  {address}")
        print(f"  inspector: https://agentverse.ai/inspect/?uri={quote(f'http://127.0.0.1:{port}')}&address={address}")
        print(f"  profile:   https://agentverse.ai/agents/details/{address}/profile")
    allowed = chat_allowlist()
    print(f"\nTEAM_ALLOWLIST_ADDRESSES: {len(allowed)} address(es)."
          + ("" if allowed else " Nobody can run a round from chat until yours is added (evidence/m08/README.md)."))
    print("Chat at https://asi1.ai: search for the agent's name or paste its address.\n")


def start(name: str, module: str, env: dict) -> subprocess.Popen:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = (LOGS / f"{name}.log").open("w")
    print(f"starting {name:17} log: {log.name}")
    return subprocess.Popen([sys.executable, "-m", module], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)


def wait_for(what: str, ready, timeout: float, procs: list[subprocess.Popen]) -> None:
    deadline = time.monotonic() + timeout
    while not ready():
        if any(p.poll() is not None for p in procs):
            raise SystemExit(f"a process exited while waiting for {what}; see {LOGS}")
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out waiting for {what}; see {LOGS}")
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--addresses", action="store_true", help="only print the addresses and links")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up even when piped to a file
    if args.addresses:
        print_addresses()
        return

    url = engine_http_url()
    env = {**os.environ, "ENGINE_SOURCE": "http", "ENGINE_HTTP_URL": url, "PYTHONUNBUFFERED": "1"}
    procs: list[subprocess.Popen] = []
    try:
        if engine_reachable(url):
            print(f"engine already running at {url}; using it")
        else:
            procs.append(start("engine", "engine.serve", env))
            wait_for(f"the engine at {url}", lambda: engine_reachable(url), ENGINE_START_TIMEOUT_S, procs)
            print(f"engine is up at {url}")
        for name, seed_name, _ in AGENTS:
            procs.append(start(name, f"integrations.asi_one.{seed_name}", env))
        wait_for("both mailboxes to link",
                 lambda: all("Mailbox linked" in (LOGS / f"{name}.log").read_text() for name, _, _ in AGENTS),
                 MAILBOX_TIMEOUT_S, procs)
        print("both mailboxes linked")
        print_addresses()
        print("Running. Ctrl-C stops everything this command started.")
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        print(f"a process exited; see {LOGS}")
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.send_signal(signal.SIGINT)
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
