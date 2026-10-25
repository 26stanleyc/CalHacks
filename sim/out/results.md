# Tag-Team Arena vs swarm: toy simulation results

10 seeds × 20 rounds, 18 attackers × 20 attempts per round in every condition. Same defense learner, same exam. Lower $ = better defense. Numbers are mean ± std across seeds.

## Headline: 60-rule defense, attackers start near profitable spots

| Training method | Exploitability, avg shelf ($/attempt) | Exploitability, worst shelf | Shelves defended (of 24) | Held-out attackers: $ lost/attempt | Held-out catch rate | Catch rate on own training attackers | Fresh swarm vs final defense |
|---|---|---|---|---|---|---|---|
| *No training (untrained defense)* | 1.36 ± 0.15 | 4.02 ± 0.95 | 0 | 0.817 ± 0.109 | 15% | n/a | 2.65 ± 1.33 |
| Swarm | 1.39 ± 0.15 | 4.35 ± 0.96 | 0.0 | 0.806 ± 0.111 | 17% | 69% | 2.62 ± 1.36 |
| Swarm + shelves | 1.25 ± 0.12 | 4.14 ± 0.83 | 0.0 | 0.569 ± 0.093 | 29% | 60% | 2.32 ± 1.62 |
| Tag-team, no shelves | 1.27 ± 0.16 | 4.23 ± 1.07 | 2.5 | 0.686 ± 0.097 | 28% | 41% | 2.36 ± 1.55 |
| Tag-Team Arena | 0.87 ± 0.10 | 3.18 ± 1.07 | 1.8 | 0.321 ± 0.050 | 39% | 44% | 1.81 ± 1.32 |

**Tag-Team Arena vs swarm, paired by seed:**

- Avg-shelf exploitability: 38% lower; Arena better on 10/10 seeds
- Worst-shelf exploitability: 27% lower; better on 10/10 seeds
- Held-out attacker losses: 60% lower; better on 10/10 seeds
- Fresh swarm against the final defense: 31% lower; better on 8/10 seeds

## Sensitivity: does the result survive other settings?

Avg-shelf exploitability at round 20 ($/attempt). Untrained defense: 1.36.

| Setting | Swarm | Swarm + shelves | Tag-team, no shelves | Tag-Team Arena | Arena better than swarm (seeds) |
|---|---|---|---|---|---|
| 30 rules, informed start | 1.40 | 1.31 | 1.31 | 1.09 | 10/10 |
| 60 rules, informed start | 1.39 | 1.25 | 1.27 | 0.87 | 10/10 |
| 120 rules, informed start | 1.32 | 1.19 | 1.01 | 0.41 | 10/10 |
| 30 rules, random start | 1.40 | 1.37 | 1.35 | 1.08 | 10/10 |
| 60 rules, random start | 1.39 | 1.35 | 1.34 | 0.85 | 10/10 |
| 120 rules, random start | 1.37 | 1.33 | 1.08 | 0.44 | 10/10 |

## Read before quoting these numbers

- This is a stylized toy model written to compare training curricula, not a run of the real system. Dollar figures are model units.
- The exam's fresh attacker uses the same 200-try sandbox search as Tag-Team spawners, which favors the Arena. The held-out attackers and the fresh swarm don't share that bias, and the Arena still wins on both.
- Where a baseline wins: on 'shelves defended' at 60 rules, Tag-team-without-shelves (2.5) beats the Arena (1.8). Both are low, since 60 rules can't cover 24 shelves.
- The swarm-trained defense is no better than an untrained one. It catches 69% of its own training attackers but only 17% of held-out ones: it overfits to the few tricks the swarm converges on.
- The Arena's gain needs both pieces. Shelves alone or sandbox-tuned spawning alone each help only a little.
- Both tag-team variants get somewhat worse after round 10, when the defense hits its 60-rule cap and starts retiring rules. A real rule budget will have the same effect.

Reproduce: `for c in 30 60 120; do for i in informed random; do INIT=$i CAP=$c python3 sim/tagteam_vs_swarm.py sim/out/${i}_cap$c.json; done; done && python3 sim/analyze.py`
