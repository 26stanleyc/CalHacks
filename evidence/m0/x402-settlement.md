# x402 testnet settlement (M0 probe / E4, evidence for M13)

Real settlements on **Base Sepolia** (`eip155:84532`) through the public x402 facilitator
at `https://x402.org/facilitator`, which needs no API key on testnet.

Testnet USDC only. These tokens have no value.

## Transactions

Each is one cent of USDC (10,000 base units; USDC has 6 decimals) from the buyer wallet to
the merchant wallet, settled by `integrations.x402_settlement.X402Settlement` in real mode.

| What | Hash |
|---|---|
| First end-to-end check of the signing + verify + settle path | [`0xff190e58...a7117`](https://sepolia.basescan.org/tx/0xff190e580ba8fc59ba8d0a04e8acede527d7e433b768a06eb6f109bd90ba7117) |
| Through the `Settlement` contract implementation | [`0x7e99813c...5c611`](https://sepolia.basescan.org/tx/0x7e99813c1dde11e559ee0693909a3ae8ca4f837dfbb374eb622c6b7932c5c611) |
| Re-check after the spend-cap change | [`0x8ee6a8ad...46425`](https://sepolia.basescan.org/tx/0x8ee6a8ad5a9a33821ef075446661fe8e84bad68c8a99503b2d26b90a16746425) |

**M13's manual check** ("one real x402 testnet transaction hash opens on a block explorer")
is satisfied by any of these.

## Balances after the three settlements

| Wallet | USDC |
|---|---|
| Buyer (`TESTNET_WALLET_ADDRESS`) | 0.97 |
| Merchant (`MERCHANT_WALLET_ADDRESS`) | 0.03 |

Value really moved: the buyer started at 1.00 and the merchant at 0.00.

## How it works

1. `GET /supported` confirms the facilitator offers `exact` on `eip155:84532`.
2. The buyer signs an **EIP-3009 `transferWithAuthorization`** for the USDC contract
   `0x036CbD53842c5426634e7929541eC2318f3dCF7e`. Signing is offline: the token's EIP-712
   domain (`name: USDC`, `version: 2`) is passed in `PaymentRequirements.extra`, so no RPC
   call is needed.
3. `POST /verify` checks the authorization before anything is spent.
4. `POST /settle` submits it. **The facilitator pays the gas**, so the buyer wallet needs
   test USDC but no ETH.

## Topping up

Only needed if a settlement fails with an insufficient-balance reason:

```bash
uv run python scripts/fund_wallet.py 0x<the buyer address in .env>
```

The buyer wallet holds about 97 cents, which is roughly 97 more one-cent demo settlements.
`X402_MAX_SETTLE_CENTS` (default 100) caps a single real settlement, so a bug cannot drain
it in one call. The cap applies in real mode only: the precompute runs use stub mode, where
simulated amounts run well past it.
