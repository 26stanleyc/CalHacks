"""Generate a throwaway Base Sepolia wallet, save it to .env, and fund it from the CDP faucet.

Fills X402_FACILITATOR_URL, TESTNET_WALLET_PRIVATE_KEY and TESTNET_WALLET_ADDRESS in .env.
Testnet only: never put a key that controls real funds here. Skips generation if a key
is already set, so it is safe to rerun.
Run from the repo root:  .venv/bin/python scripts/make_local_wallet.py
"""
import asyncio
import os
from pathlib import Path

import certifi
from cdp import CdpClient
from dotenv import dotenv_values, load_dotenv
from eth_account import Account
from fund_wallet import request_funds

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
# Free public x402 facilitator for testnets (Base Sepolia, eip155:84532). No API key needed.
FACILITATOR_URL = "https://x402.org/facilitator"


def set_env(updates: dict[str, str]) -> None:
    """Set KEY=value lines in .env, replacing existing ones and keeping everything else."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    remaining = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    lines += [f"{k}={v}" for k, v in remaining.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n")
    ENV_PATH.chmod(0o600)


async def main() -> None:
    existing = dotenv_values(ENV_PATH)
    if existing.get("TESTNET_WALLET_PRIVATE_KEY"):
        address = existing["TESTNET_WALLET_ADDRESS"]
        print(f"Key already set, reusing wallet {address}")
        set_env({"X402_FACILITATOR_URL": existing.get("X402_FACILITATOR_URL") or FACILITATOR_URL})
    else:
        acct = Account.create()
        address = acct.address
        set_env(
            {
                "X402_FACILITATOR_URL": FACILITATOR_URL,
                "TESTNET_WALLET_PRIVATE_KEY": acct.key.hex()
                if acct.key.hex().startswith("0x")
                else "0x" + acct.key.hex(),
                "TESTNET_WALLET_ADDRESS": address,
            }
        )
        print(f"Generated new testnet wallet {address} and saved it to .env")

    load_dotenv(ENV_PATH)
    print(f"Explorer:  https://sepolia.basescan.org/address/{address}")
    async with CdpClient() as cdp:
        await request_funds(cdp, address)


if __name__ == "__main__":
    asyncio.run(main())
