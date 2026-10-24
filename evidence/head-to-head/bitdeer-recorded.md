Seeds 42; 4 rounds; Bitdeer models; new attackers after round 3: 4; LLM calls 300, tokens 2,001,227, failed calls 1; 7.6 min per round. Mean ± standard deviation across seeds.

| contender | new attacks, first round | new attacks, first 3 rounds | all fraud | honest shoppers | total cost | mean FP | max FP |
|---|---|---|---|---|---|---|---|
| full | $825 ± $0 | $825 ± $0 | $46,760 ± $0 | $244 ± $0 | $47,004 ± $0 | 1.67% ± 0.00% | 2.33% |
| xgboost_retrained | $289 ± $0 | $289 ± $0 | $44,030 ± $0 | $710 ± $0 | $44,740 ± $0 | 3.93% ± 0.00% | 7.05% |
| xgboost_frozen | $851 ± $0 | $851 ± $0 | $65,331 ± $0 | $963 ± $0 | $66,293 ± $0 | 5.02% ± 0.00% | 7.05% |
| static_rules | $1,250 ± $0 | $1,250 ± $0 | $78,062 ± $0 | $163 ± $0 | $78,225 ± $0 | 0.75% ± 0.00% | 2.35% |
| rules_plus_xgboost | $242 ± $0 | $242 ± $0 | $35,753 ± $0 | $873 ± $0 | $36,626 ± $0 | 4.69% ± 0.00% | 7.38% |

Costs under the cost matrix: fraud columns are missed fraud plus friction on caught fraud; "honest shoppers" is false blocks, step-up friction and abandoned purchases; total is both.

Recorded in the team database (pick these in the dashboard): h2h-real-42-202610241623
