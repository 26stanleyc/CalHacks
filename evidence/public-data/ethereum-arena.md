# TagTeam on real Ethereum phishing data (the arena's "Real Ethereum phishing" data set)

## Data
XBlock's Ethereum Phishing Transaction Network (Chen et al., "Phishing scams detection in Ethereum transaction
network", ACM TOIT 21(1), 2021; xblock.pro/#/dataset/13; Kaggle mirror xblock/ethereum-phishing-transaction-network,
MulDiGraph.pkl, 1,257,580,711 bytes). 2,973,489 addresses, 13,551,303 real Ethereum transactions (Aug 2015 to Jan
2019), 1,165 addresses Etherscan labels as phishing. Crawled two hops out from those addresses, so it is traffic
around known scams, not a random sample of Ethereum. No license is stated (XBlock or Kaggle); the transactions are
public on-chain data. Kept outside the repo (~/calhacks-sandbox/eth/); only the derived replay is in the arena.

Loaded with engine/eth_phishing.py: a restricted unpickler (stand-in graph class and builtin containers only;
networkx never imported), run with python -I. The file asked for nothing outside that list. 40 s, 5.6 GB RAM.

## How the fields map to the feature service
| Data | Ours | Note |
|---|---|---|
| sender address | agent_id and principal_id | one address = one payer; we can't tell a person's several addresses apart |
| receiver address | merchant_id | |
| amount (ETH) | amount_cents in hundredths of an ETH | the arena shows Ξ |
| timestamp | ts | real UTC dates; one day per round, three 8-hour waves |
| `isp` on the receiver | is_fraud | a payment into a phishing address = a victim paying a scammer |
| (none) | identity, mandate, memo, category | not in the data: identity reads "unknown" for everyone, mandate features stay empty |

Left out (approximations): 9,733,056 transactions under Ξ0.005 (72%; nearly all zero-value contract calls, which
move no ETH), and 7,787 payments sent by phishing addresses (scammers moving stolen money on: neither theft nor
honest). Step-up outcomes aren't in the data, so they use the engine's simulated rule for both contenders. No LLM
investigator.

## Runs (all on the same test week, Mar 5-10 2018: 49,389 payments, 979 into phishing addresses, Ξ1,018)
The week was picked by the runner as the 6-day stretch with the most phishing payments under 150,000 payments, before
any result was seen; it was then held fixed. Net = honest money through minus money stolen.

| Setup | TagTeam net | XGBoost net | Phishing stopped (TagTeam / XGBoost) | Honest flagged (TagTeam / XGBoost) |
|---|---|---|---|---|
| No training; XGBoost fitted on day 1, mock reflection | Ξ459,999 | Ξ465,250 | 0 / 15 | 603 / 20 |
| Same, real Bitdeer reflection (GLM-5.3) | Ξ459,999 | Ξ465,250 | 0 / 15 | 603 / 20 |
| Same, plus a 1-day label-free shadow burn-in (Mar 4) | Ξ461,556 | Ξ465,248 | 0 / 9 | 404 / 16 |
| **7 training days (Feb 26 - Mar 4, 61,621 payments), real reflection** (in the arena) | **Ξ465,257** | **Ξ465,246** | **58 / 0** | **32 / 0** |

Why the first three lose: TagTeam's seed rules were written for our simulated marketplace and fire on ordinary
Ethereum traffic (many fresh addresses paying one exchange address looks like graph.farm_swarm). 595 honest payments
were flagged on day 1, before any learning; the loop retired those rules after one round and false alarms went to 0.
Neither defense learned to catch phishing from one day: a payment into a phishing address looks like any transfer to
a new address when all you have is sender, receiver, amount and time.

The arena's run: both are trained on the same 7 labelled days before the test week (XGBoost fitted once and frozen;
TagTeam learning one day at a time, as it runs live), then decide the test week. TagTeam keeps learning during the
test week; XGBoost stays frozen (the arena's "trained once" baseline). TagTeam stops 58 phishing payments (56 on
Mar 8), all after learning from the test week's earlier days, at the price of 32 false alarms. The margin is small:
Ξ11.66 of net on Ξ465k, and 5% less phishing money lost (Ξ963 vs Ξ1,018). Two of the models' replies were invalid
JSON and fell back to the mock for that lane and round.

No parameter was tuned to this week. The burn-in's threshold is the learning loop's existing false-alarm budget (2%);
the training length (7 days) was set before the run.

Reproduce (engine branch stanley/real-data-engine):

    .venv/bin/python -I engine/eth_phishing.py ~/calhacks-sandbox/eth/raw/MulDiGraph.pkl ~/calhacks-sandbox/eth/arrays
    uv run python -m engine.public_replay ethereum --data ~/calhacks-sandbox/eth/arrays --start 2018-03-05 --days 6 \
        --rounds 6 --train-days 7 --real --out ../arena-real-data/arena/public/eth

Run id: eth-phishing-2018-03-05-6d-6r-real-train7d.
