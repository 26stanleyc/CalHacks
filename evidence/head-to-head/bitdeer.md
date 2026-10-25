Seeds 42; 6 rounds; Bitdeer models; new attackers after round 3: 12; LLM calls 491, tokens 3,076,743, failed calls 2. Mean ± standard deviation across seeds.

| contender | new attacks, first round | new attacks, first 3 rounds | all fraud | honest shoppers | total cost | mean FP | max FP |
|---|---|---|---|---|---|---|---|
| full | $3,506 ± $0 | $8,187 ± $0 | $65,813 ± $0 | $211 ± $0 | $66,025 ± $0 | 0.73% ± 0.00% | 2.01% |
| xgboost_retrained | $2,024 ± $0 | $5,822 ± $0 | $39,623 ± $0 | $350 ± $0 | $39,973 ± $0 | 1.95% ± 0.00% | 6.67% |
| xgboost_frozen | $4,488 ± $0 | $15,113 ± $0 | $67,092 ± $0 | $795 ± $0 | $67,887 ± $0 | 4.12% ± 0.00% | 6.67% |
| static_rules | $8,229 ± $0 | $23,817 ± $0 | $105,594 ± $0 | $356 ± $0 | $105,950 ± $0 | 0.73% ± 0.00% | 2.01% |
| rules_plus_xgboost | $1,863 ± $0 | $5,234 ± $0 | $30,212 ± $0 | $706 ± $0 | $30,917 ± $0 | 2.67% ± 0.00% | 6.67% |

Costs under the cost matrix: fraud columns are missed fraud plus friction on caught fraud; "honest shoppers" is false blocks, step-up friction and abandoned purchases; total is both.
