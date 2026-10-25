PaySim, TRANSFER and CASH_OUT, 10% sample: 276,308 payments (839 fraud) over 31 days. Trained on days 1-15, scored on days 16-31.

| contender | PR-AUC | recall at 1% FPR | fraud caught | honest flagged | missed-fraud cost | honest-shopper cost | total cost |
|---|---|---|---|---|---|---|---|
| ours | 0.102 | 21.2% | 5.4% | 0.09% | $483,729,569 | $18,856,772 | $502,586,341 |
| xgboost_trained | 0.044 | 13.2% | 0.0% | 0.00% | $611,226,738 | $0 | $611,226,738 |
| xgboost_retrained | 0.085 | 16.3% | 0.0% | 0.00% | $611,226,738 | $0 | $611,226,738 |
| static_rules | 0.008 | 0.0% | 0.0% | 0.00% | $611,226,738 | $0 | $611,226,738 |
| rules_plus_xgboost | 0.085 | 16.3% | 0.0% | 0.00% | $611,226,738 | $0 | $611,226,738 |
