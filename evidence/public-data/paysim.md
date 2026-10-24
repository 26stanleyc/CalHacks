PaySim, TRANSFER and CASH_OUT, 10% sample: 276,308 payments (839 fraud) over 31 days. Trained on days 1-15, scored on days 16-31.

| contender | PR-AUC | recall at 1% FPR | fraud caught | honest flagged | missed-fraud cost | honest-shopper cost | total cost |
|---|---|---|---|---|---|---|---|
| ours | 0.080 | 16.7% | 0.7% | 0.01% | $583,226,738 | $15,000,015 | $598,226,753 |
| xgboost_trained | 0.044 | 13.2% | 0.0% | 0.00% | $611,226,738 | $0 | $611,226,738 |
| xgboost_retrained | 0.085 | 16.3% | 0.0% | 0.00% | $611,226,738 | $0 | $611,226,738 |
| static_rules | 0.008 | 0.0% | 0.0% | 0.00% | $611,226,738 | $0 | $611,226,738 |
| rules_plus_xgboost | 0.085 | 16.3% | 0.0% | 0.00% | $611,226,738 | $0 | $611,226,738 |
