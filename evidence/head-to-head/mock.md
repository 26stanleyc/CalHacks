Seeds 42, 43, 44; 10 rounds; mock models; new attackers after round 3: 114; LLM calls 3,859, tokens 0, failed calls 0. Mean ± standard deviation across seeds.

| contender | new attacks, first round | new attacks, first 3 rounds | all fraud | honest shoppers | total cost | mean FP | max FP |
|---|---|---|---|---|---|---|---|
| full | $25,322 ± $5,707 | $34,052 ± $7,899 | $125,551 ± $17,885 | $448 ± $302 | $125,999 ± $17,727 | 0.66% ± 0.15% | 2.68% |
| xgboost_retrained | $11,198 ± $1,172 | $15,098 ± $1,700 | $69,091 ± $3,258 | $888 ± $93 | $69,979 ± $3,313 | 1.29% ± 0.17% | 5.37% |
| xgboost_frozen | $51,006 ± $17,088 | $68,688 ± $24,336 | $178,851 ± $39,798 | $4,391 ± $685 | $183,241 ± $40,269 | 4.60% ± 0.43% | 8.36% |
| static_rules | $51,564 ± $10,708 | $69,953 ± $14,522 | $204,992 ± $31,904 | $931 ± $297 | $205,922 ± $32,128 | 1.15% ± 0.33% | 2.01% |
| rules_plus_xgboost | $10,897 ± $1,101 | $14,751 ± $1,644 | $57,665 ± $968 | $1,772 ± $335 | $59,437 ± $1,303 | 2.37% ± 0.19% | 6.38% |

Costs under the cost matrix: fraud columns are missed fraud plus friction on caught fraud; "honest shoppers" is false blocks, step-up friction and abandoned purchases; total is both.
