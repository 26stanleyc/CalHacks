Seeds 42; 20 rounds; Bitdeer models; new attackers after round 3: 73; LLM calls 1,401, tokens 8,845,469, failed calls 7; 7.9 min per round. Mean ± standard deviation across seeds.

| contender | new attacks, first round | new attacks, first 3 rounds | all fraud | honest shoppers | total cost | mean FP | max FP |
|---|---|---|---|---|---|---|---|
| full | $11,515 ± $0 | $19,119 ± $0 | $75,836 ± $0 | $631 ± $0 | $76,468 ± $0 | 0.80% ± 0.00% | 2.01% |
| xgboost_retrained | $11,098 ± $0 | $18,251 ± $0 | $81,049 ± $0 | $493 ± $0 | $81,543 ± $0 | 1.02% ± 0.00% | 7.00% |
| xgboost_frozen | $67,789 ± $0 | $154,647 ± $0 | $475,047 ± $0 | $3,884 ± $0 | $478,931 ± $0 | 6.73% ± 0.00% | 9.33% |
| static_rules | $62,847 ± $0 | $146,334 ± $0 | $472,675 ± $0 | $1,655 ± $0 | $474,330 ± $0 | 0.95% ± 0.00% | 2.35% |
| rules_plus_xgboost | $8,564 ± $0 | $15,583 ± $0 | $66,063 ± $0 | $2,100 ± $0 | $68,164 ± $0 | 1.96% ± 0.00% | 7.00% |

Costs under the cost matrix: fraud columns are missed fraud plus friction on caught fraud; "honest shoppers" is false blocks, step-up friction and abandoned purchases; total is both.

Recorded in the team database (pick these in the dashboard): h2h-real-42-202610241732
