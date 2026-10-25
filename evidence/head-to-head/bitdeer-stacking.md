Seeds 100, 101, 102; 6 rounds; Bitdeer models; new attackers after round 3: 45; LLM calls 1,622, tokens 10,488,906, failed calls 18. Mean ± standard deviation across seeds.

| contender | new attacks, first round | new attacks, first 3 rounds | all fraud | honest shoppers | total cost | mean FP | max FP |
|---|---|---|---|---|---|---|---|
| full | $11,873 ± $9,365 | $15,028 ± $9,341 | $58,067 ± $1,179 | $245 ± $88 | $58,312 ± $1,267 | 1.14% ± 0.08% | 2.67% |
| xgboost_retrained | $12,317 ± $12,227 | $16,334 ± $14,037 | $65,574 ± $19,576 | $801 ± $549 | $66,375 ± $19,027 | 3.47% ± 1.59% | 13.00% |
| xgboost_frozen | $21,206 ± $15,936 | $33,590 ± $22,737 | $102,120 ± $33,412 | $1,905 ± $1,424 | $104,025 ± $32,269 | 7.25% ± 4.39% | 16.11% |
| static_rules | $21,592 ± $12,799 | $34,517 ± $15,837 | $110,824 ± $6,298 | $415 ± $52 | $111,239 ± $6,249 | 0.78% ± 0.06% | 1.67% |
| rules_plus_xgboost | $11,787 ± $11,663 | $15,207 ± $12,564 | $54,308 ± $13,625 | $1,214 ± $576 | $55,522 ± $13,051 | 4.23% ± 1.52% | 14.00% |

Costs under the cost matrix: fraud columns are missed fraud plus friction on caught fraud; "honest shoppers" is false blocks, step-up friction and abandoned purchases; total is both.
