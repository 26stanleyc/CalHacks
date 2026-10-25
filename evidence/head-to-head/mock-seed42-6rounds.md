Seeds 42; 6 rounds; mock models; new attackers after round 3: 18; LLM calls 634, tokens 0, failed calls 0. Mean ± standard deviation across seeds.

| contender | new attacks, first round | new attacks, first 3 rounds | all fraud | honest shoppers | total cost | mean FP | max FP |
|---|---|---|---|---|---|---|---|
| full | $16,284 ± $0 | $19,034 ± $0 | $98,217 ± $0 | $77 ± $0 | $98,294 ± $0 | 0.39% ± 0.00% | 1.01% |
| xgboost_retrained | $3,223 ± $0 | $4,198 ± $0 | $51,904 ± $0 | $687 ± $0 | $52,591 ± $0 | 1.90% ± 0.00% | 5.37% |
| xgboost_frozen | $16,407 ± $0 | $21,581 ± $0 | $88,641 ± $0 | $1,940 ± $0 | $90,580 ± $0 | 4.13% ± 0.00% | 6.02% |
| static_rules | $23,324 ± $0 | $28,490 ± $0 | $120,202 ± $0 | $345 ± $0 | $120,546 ± $0 | 0.73% ± 0.00% | 2.01% |
| rules_plus_xgboost | $3,155 ± $0 | $4,046 ± $0 | $42,687 ± $0 | $1,032 ± $0 | $43,719 ± $0 | 2.62% ± 0.00% | 6.38% |

Costs under the cost matrix: fraud columns are missed fraud plus friction on caught fraud; "honest shoppers" is false blocks, step-up friction and abandoned purchases; total is both.
