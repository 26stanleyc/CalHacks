Seeds 100, 101, 102; 6 rounds; mock models; new attackers after round 3: 54; LLM calls 1,930, tokens 0, failed calls 0. Mean ± standard deviation across seeds.

| contender | new attacks, first round | new attacks, first 3 rounds | all fraud | honest shoppers | total cost | mean FP | max FP |
|---|---|---|---|---|---|---|---|
| full | $7,495 ± $897 | $7,986 ± $1,076 | $52,153 ± $6,820 | $299 ± $199 | $52,452 ± $6,930 | 0.84% ± 0.39% | 4.35% |
| xgboost_retrained | $8,811 ± $3,211 | $9,367 ± $3,428 | $59,093 ± $7,020 | $594 ± $167 | $59,686 ± $7,137 | 1.50% ± 0.49% | 4.70% |
| xgboost_frozen | $24,497 ± $1,603 | $29,880 ± $3,806 | $99,511 ± $10,517 | $1,544 ± $770 | $101,054 ± $10,401 | 3.22% ± 1.40% | 7.72% |
| static_rules | $26,591 ± $1,624 | $31,974 ± $2,581 | $107,654 ± $15,555 | $424 ± $47 | $108,078 ± $15,586 | 0.84% ± 0.10% | 1.67% |
| rules_plus_xgboost | $8,557 ± $3,256 | $9,113 ± $3,469 | $47,801 ± $9,447 | $1,018 ± $214 | $48,819 ± $9,603 | 2.34% ± 0.47% | 5.03% |

Costs under the cost matrix: fraud columns are missed fraud plus friction on caught fraud; "honest shoppers" is false blocks, step-up friction and abandoned purchases; total is both.
