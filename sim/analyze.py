"""Summarize sim/out/*.json into sim/out/results.md and sim/out/chart.png."""
import json, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.argv = ["x"]
exec(open("sim/tagteam_vs_swarm.py").read().split("if __name__")[0])

NAMES = {"swarm": "Swarm", "swarm+shelves": "Swarm + shelves",
         "tagteam-noshelf": "Tag-team, no shelves", "tagteam-arena": "Tag-Team Arena"}
COLORS = {"swarm": "#eb6834", "swarm+shelves": "#eda100", "tagteam-noshelf": "#6250d6", "tagteam-arena": "#2a78d6"}
# metric columns: 0 exploit_max, 1 exploit_mean, 2 defended, 3 heldout $, 4 train catch, 5 heldout catch


def untrained():
    out = []
    for seed in range(SEEDS):
        w = World(np.random.default_rng(seed)); d = Defense(w)
        r = np.random.default_rng(10_000 + seed); ho = []
        for _ in range(120):
            s = int(r.integers(S)); k = r.integers(3)
            ho.append((s, np.clip(w.peaks[s, k] + r.normal(0, 0.1, D), 0, 1)))
        mx, mn, _ = exam(np.random.default_rng(20_000 + seed), w, d, np.full(S, 1e9))
        out.append((mx, mn, 0, heldout_static(w, d, ho), np.nan, heldout_catch(d, ho),
                    heldout_swarm(np.random.default_rng(30_000 + seed), w, d)))
    return np.array(out)


def load(tag):
    r = json.load(open(f"sim/out/{tag}.json"))
    return {c: (np.array(v["curves"]), np.array(v["heldout_swarm"])) for c, v in r.items()}


def ms(x, fmt="{:.2f}"): return (fmt + " ± " + fmt).format(np.mean(x), np.std(x))


base = untrained()
lines = ["# Tag-Team Arena vs swarm: toy simulation results", "",
         f"{SEEDS} seeds × {ROUNDS} rounds, 18 attackers × 20 attempts per round in every condition. "
         "Same defense learner, same exam. Lower $ = better defense. Numbers are mean ± std across seeds.", ""]

for tag, title in [("informed_cap60", "Headline: 60-rule defense, attackers start near profitable spots")]:
    R = load(tag)
    lines += [f"## {title}", "",
              "| Training method | Exploitability, avg shelf ($/attempt) | Exploitability, worst shelf | Shelves defended (of 24) | Held-out attackers: $ lost/attempt | Held-out catch rate | Catch rate on own training attackers | Fresh swarm vs final defense |",
              "|---|---|---|---|---|---|---|---|",
              f"| *No training (untrained defense)* | {ms(base[:,1])} | {ms(base[:,0])} | 0 | {ms(base[:,3],'{:.3f}')} | {np.mean(base[:,5]):.0%} | n/a | {ms(base[:,6])} |"]
    for c, (cur, hs) in R.items():
        f = cur[:, -1]
        lines.append(f"| {NAMES[c]} | {ms(f[:,1])} | {ms(f[:,0])} | {np.mean(f[:,2]):.1f} | {ms(f[:,3],'{:.3f}')} | {np.mean(f[:,5]):.0%} | {np.mean(f[:,4]):.0%} | {ms(hs)} |")
    sw, tt = R["swarm"][0][:, -1], R["tagteam-arena"][0][:, -1]
    hsw, htt = R["swarm"][1], R["tagteam-arena"][1]
    lines += ["", "**Tag-Team Arena vs swarm, paired by seed:**", "",
              f"- Avg-shelf exploitability: {1 - tt[:,1].mean()/sw[:,1].mean():.0%} lower; Arena better on {(tt[:,1] < sw[:,1]).sum()}/{SEEDS} seeds",
              f"- Worst-shelf exploitability: {1 - tt[:,0].mean()/sw[:,0].mean():.0%} lower; better on {(tt[:,0] < sw[:,0]).sum()}/{SEEDS} seeds",
              f"- Held-out attacker losses: {1 - tt[:,3].mean()/sw[:,3].mean():.0%} lower; better on {(tt[:,3] < sw[:,3]).sum()}/{SEEDS} seeds",
              f"- Fresh swarm against the final defense: {1 - htt.mean()/hsw.mean():.0%} lower; better on {(htt < hsw).sum()}/{SEEDS} seeds", ""]

lines += ["## Sensitivity: does the result survive other settings?", "",
          "Avg-shelf exploitability at round 20 ($/attempt). Untrained defense: " + f"{np.mean(base[:,1]):.2f}.", "",
          "| Setting | Swarm | Swarm + shelves | Tag-team, no shelves | Tag-Team Arena | Arena better than swarm (seeds) |",
          "|---|---|---|---|---|---|"]
for init in ("informed", "random"):
    for cap in (30, 60, 120):
        R = load(f"{init}_cap{cap}")
        row = [f"{np.mean(R[c][0][:, -1, 1]):.2f}" for c in NAMES]
        wins = (R["tagteam-arena"][0][:, -1, 1] < R["swarm"][0][:, -1, 1]).sum()
        lines.append(f"| {cap} rules, {init} start | " + " | ".join(row) + f" | {wins}/{SEEDS} |")
lines += ["", "## Read before quoting these numbers", "",
          "- This is a stylized toy model written to compare training curricula, not a run of the real system. Dollar figures are model units.",
          "- The exam's fresh attacker uses the same 200-try sandbox search as Tag-Team spawners, which favors the Arena. The held-out attackers and the fresh swarm don't share that bias, and the Arena still wins on both.",
          "- Where a baseline wins: on 'shelves defended' at 60 rules, Tag-team-without-shelves (2.5) beats the Arena (1.8). Both are low, since 60 rules can't cover 24 shelves.",
          "- The swarm-trained defense is no better than an untrained one. It catches 69% of its own training attackers but only 17% of held-out ones: it overfits to the few tricks the swarm converges on.",
          "- The Arena's gain needs both pieces. Shelves alone or sandbox-tuned spawning alone each help only a little.",
          "- Both tag-team variants get somewhat worse after round 10, when the defense hits its 60-rule cap and starts retiring rules. A real rule budget will have the same effect.",
          "", "Reproduce: `for c in 30 60 120; do for i in informed random; do INIT=$i CAP=$c python3 sim/tagteam_vs_swarm.py sim/out/${i}_cap$c.json; done; done && python3 sim/analyze.py`"]
open("sim/out/results.md", "w").write("\n".join(lines) + "\n")
print("\n".join(lines))

# chart: avg-shelf exploitability per round, headline setting
R = load("informed_cap60")
fig, ax = plt.subplots(figsize=(8, 4.5), dpi=160)
x = np.arange(1, ROUNDS + 1)
ax.axhline(np.mean(base[:, 1]), color="#8a8984", lw=1.5, ls=(0, (4, 3)))
ax.text(5, np.mean(base[:, 1]) + 0.08, "Untrained defense (dashed)", color="#52514e", fontsize=9, va="bottom")
ends = []
for c in NAMES:
    y = R[c][0][:, :, 1]
    m, sd = y.mean(0), y.std(0)
    ax.fill_between(x, m - sd, m + sd, color=COLORS[c], alpha=0.12, lw=0)
    ax.plot(x, m, color=COLORS[c], lw=2)
    ends.append((m[-1], c))
ends.sort()
prev = -1
for yv, c in ends:  # direct labels, nudged apart
    yv = max(yv, prev + 0.07); prev = yv
    ax.text(ROUNDS + 0.3, yv, NAMES[c], color="#0b0b0b", fontsize=9, va="center")
    ax.plot([ROUNDS + 0.05, ROUNDS + 0.25], [yv, yv], color=COLORS[c], lw=2)
ax.set_xlim(1, ROUNDS + 5.5); ax.set_ylim(0, None)
ax.set_xticks([1, 5, 10, 15, 20])
ax.set_xlabel("Training round", color="#52514e")
ax.set_ylabel("$ a fresh attacker extracts per attempt\n(avg over 24 shelves)", color="#52514e")
ax.set_title("Exploitability during training (lower is better)", loc="left", fontsize=12, color="#0b0b0b")
for s in ("top", "right"): ax.spines[s].set_visible(False)
for s in ("left", "bottom"): ax.spines[s].set_color("#c3c2b7")
ax.tick_params(colors="#52514e")
ax.grid(axis="y", color="#e6e5e0", lw=0.8); ax.set_axisbelow(True)
fig.text(0.01, 0.01, f"Toy simulation, {SEEDS} seeds, shaded band = ±1 std. 60-rule defense, 18 attackers/round in every condition.",
         fontsize=7.5, color="#52514e")
fig.tight_layout(rect=(0, 0.03, 1, 1))
fig.savefig("sim/out/chart.png")
