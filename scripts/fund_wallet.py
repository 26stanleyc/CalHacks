"""Request Base Sepolia test funds from the CDP faucet, for a named CDP wallet or any address.

Testnet only: Base Sepolia tokens have no real value. Reads CDP_* secrets from .env.
Run from the repo root:
  .venv/bin/python scripts/fund_wallet.py [wallet-name | 0xAddress]
"""
import asyncio
import os
import sys

import certifi
from cdp import CdpClient
from dotenv import load_dotenv

# python.org builds of Python on macOS ship without a CA bundle; use certifi's.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

NETWORK = "base-sepolia"
TOKENS = ["eth", "usdc"]


async def request_funds(cdp: CdpClient, address: str) -> None:
    for token in TOKENS:
        try:
            tx = await cdp.evm.request_faucet(address=address, network=NETWORK, token=token)
            print(f"  {token}: https://sepolia.basescan.org/tx/{tx}")
        except Exception as e:  # faucet limits and rate limits land here
            print(f"  {token}: faucet request failed: {e}")


async def main(target: str) -> None:
    load_dotenv()
    async with CdpClient() as cdp:
        if target.startswith("0x"):
            address = target
        else:
            account = await cdp.evm.get_or_create_account(name=target)
            address = account.address
            print(f"Wallet '{target}': {address}")
        print(f"Explorer:  https://sepolia.basescan.org/address/{address}")
        await request_funds(cdp, address)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "shopper"))
