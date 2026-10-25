Seeds 42; 4 rounds; Bitdeer models; new attackers after round 3: 4; LLM calls 348, tokens 2,161,808, failed calls 4; 9.4 min per round. Mean ± standard deviation across seeds.

| contender | new attacks, first round | new attacks, first 3 rounds | all fraud | honest shoppers | total cost | mean FP | max FP |
|---|---|---|---|---|---|---|---|
| full | $112 ± $0 | $112 ± $0 | $54,900 ± $0 | $224 ± $0 | $55,123 ± $0 | 1.76% ± 0.00% | 3.93% |
| xgboost_retrained | $110 ± $0 | $110 ± $0 | $58,818 ± $0 | $709 ± $0 | $59,528 ± $0 | 4.79% ± 0.00% | 9.91% |
| xgboost_frozen | $702 ± $0 | $702 ± $0 | $81,838 ± $0 | $1,062 ± $0 | $82,900 ± $0 | 6.88% ± 0.00% | 9.91% |
| static_rules | $1,246 ± $0 | $1,246 ± $0 | $87,917 ± $0 | $160 ± $0 | $88,077 ± $0 | 0.63% ± 0.00% | 1.24% |
| rules_plus_xgboost | $67 ± $0 | $67 ± $0 | $50,498 ± $0 | $870 ± $0 | $51,368 ± $0 | 5.41% ± 0.00% | 10.53% |

Costs under the cost matrix: fraud columns are missed fraud plus friction on caught fraud; "honest shoppers" is false blocks, step-up friction and abandoned purchases; total is both.

Recorded in the team database (pick these in the dashboard): h2h-real-42-202610250153
