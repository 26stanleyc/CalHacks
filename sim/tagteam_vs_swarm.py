"""Toy simulation: Tag-Team Arena (shelves) vs a plain attacker swarm.

A stylized model, not the real system. It isolates one question: given the same
defense learner and the same number of attackers per round, does the way we pick
training attackers change how exploitable the defense ends up?

World
  24 shelves (4 styles x 3 amounts x 2 sides). An attacker is (shelf, theta),
  theta in [0,1]^4 = its tunable numbers (amount, spacing, accounts, merchants).
  Each shelf has a value landscape (3 peaks): dollars taken per uncaught attempt.
Defense (identical in every condition)
  At most 60 rules. A rule sits on one shelf at a point in theta-space and catches
  attempts near it. After each round it adds up to 6 rules where the round's
  uncaught losses cluster; when full it retires the rule with the fewest catches
  over the last 3 rounds (spec: "rules that never catch anything are retired").
Conditions (18 attackers x 20 attempts per round in all of them)
  swarm         18 black-box attackers evolving by selection + mutation on profit
                (6 outsider personas x 3). No archive, no rule intel.
  swarm+shelves archive with shelves, re-scoring and learnability replay, but the
                6 spawn slots are black-box mutants (no sandbox tuning).
  tagteam-noshelf  6 gray-box spawners tuned against a sandbox copy (200 tries),
                plus a plain league replaying the top attackers. No shelves.
  tagteam-arena full method: shelves (top 3, 1 outsider spot, duplicate check,
                re-scoring), learnability-weighted replay (80/20), 6 spawners
                aimed at distinct shaky/empty shelves, 6 outsider lineages.
Metrics (on the frozen defense after each round)
  exploitability  a fresh attacker per shelf searches 200 tries; best $/attempt.
                  Reported as max over shelves and mean over shelves.
  shelves defended  shelves where that best attacker gets < 25% of what it would
                  get against no learned rules.
  held-out static  120 fixed "realistic" attackers drawn once per seed (near value
                  peaks, no defense knowledge), never trained on: $ lost/attempt.
  held-out swarm  a fresh black-box swarm (new seed) runs 15 generations against
                  the final frozen defense: best $/attempt it reaches.
"""
import numpy as np

S, D = 24, 4
N_ATT = 20            # attempts per attacker per round
import os
CAP, ADD = int(os.environ.get("CAP", 60)), 6      # defense rule cap, rules added per round
Q, R = 0.9, 0.15      # rule strength, rule radius
ROUNDS, SEEDS = 20, 10
TRIES = 200           # number-tuning budget (spawners and the exam)

def shelf_id(style, amt, side): return style * 6 + amt * 2 + side
def unshelf(s): return s // 6, (s % 6) // 2, s % 2

PERSONAS = [shelf_id(0, 0, 0),  # card tester: burst, <$10, vs merchants
            shelf_id(1, 1, 0),  # structurer: slow drip, $10-100
            shelf_id(2, 0, 0),  # account farmer: many accounts
            shelf_id(3, 1, 1),  # prompt injection vs buyers
            shelf_id(3, 2, 1),  # hijacker
            shelf_id(3, 1, 0)]  # malicious merchant


class World:
    def __init__(self, rng):
        amt_mult = np.array([1.0, 2.0, 4.0])
        self.base = np.array([amt_mult[unshelf(s)[1]] for s in range(S)]) * rng.lognormal(0, 0.4, S)
        self.peaks = rng.uniform(0.1, 0.9, (S, 3, D))
        self.heights = rng.uniform(0.4, 1.0, (S, 3))
        self.b = np.array([0.05 + 0.05 * unshelf(s)[1] for s in range(S)]) + rng.uniform(0, 0.1, S)

    def value(self, s, th):  # th: (n, D)
        d2 = ((th[:, None, :] - self.peaks[s][None]) ** 2).sum(-1)
        return self.base[s] * (self.heights[s][None] * np.exp(-d2 / (2 * 0.18 ** 2))).max(1)


class Defense:
    def __init__(self, world):
        self.w = world
        self.shelf = np.zeros(0, int); self.c = np.zeros((0, D)); self.hits = np.zeros((0, 3))

    def kern(self, s, th):
        m = self.shelf == s
        if not m.any(): return np.zeros((len(th), 0)), np.nonzero(m)[0]
        d2 = ((th[:, None, :] - self.c[m][None]) ** 2).sum(-1)
        return Q * np.exp(-d2 / (2 * R ** 2)), np.nonzero(m)[0]

    def p_catch(self, s, th):
        k, _ = self.kern(s, th)
        return 1 - (1 - self.w.b[s]) * np.prod(1 - k, axis=1)

    def exp_profit(self, s, th):  # expected $ per attempt for the attacker
        p = self.p_catch(s, th)
        return (1 - p) * self.w.value(s, th) - p * 0.2 * self.w.base[s]

    def learn(self, log):
        """log: list of (shelf, theta(n,D), caught(n), value(n))."""
        self.hits = np.roll(self.hits, 1, axis=1); self.hits[:, 0] = 0
        pts, loss, sh = [], [], []
        for s, th, caught, v in log:
            k, idx = self.kern(s, th[caught])
            if len(idx):  # credit catches to the strongest nearby rule
                strong = k.max(1) > 0.1
                np.add.at(self.hits[:, 0], idx[k.argmax(1)[strong]], 1)
            miss = ~caught
            pts.append(th[miss]); loss.append(v[miss]); sh.append(np.full(miss.sum(), s))
        pts, loss, sh = np.concatenate(pts), np.concatenate(loss), np.concatenate(sh)
        for _ in range(ADD):
            if loss.sum() <= 0: break
            # point whose neighbourhood (same shelf) holds the most uncovered loss
            same = sh[:, None] == sh[None]
            nb = np.exp(-((pts[:, None] - pts[None]) ** 2).sum(-1) / (2 * R ** 2)) * same
            score = nb @ loss
            i = score.argmax()
            wts = nb[i] * loss
            center = (wts[:, None] * pts).sum(0) / wts.sum()
            if len(self.shelf) >= CAP:  # retire the least useful rule
                j = self.hits.sum(1).argmin()
                keep = np.arange(len(self.shelf)) != j
                self.shelf, self.c, self.hits = self.shelf[keep], self.c[keep], self.hits[keep]
            self.shelf = np.append(self.shelf, sh[i]); self.c = np.vstack([self.c, center])
            self.hits = np.vstack([self.hits, np.zeros(3)])
            loss = loss * (1 - Q * nb[i])  # those losses are now (mostly) covered


def run_attempts(rng, world, dfn, s, th):
    th_n = np.clip(th + rng.normal(0, 0.03, (N_ATT, D)), 0, 1)
    caught = rng.random(N_ATT) < dfn.p_catch(s, th_n)
    v = world.value(s, th_n)
    profit = (v * ~caught).sum() - caught.sum() * 0.2 * world.base[s]
    return (s, th_n, caught, v), profit / N_ATT


def tune(rng, dfn, s, seed_th=None):
    """Sandbox number tuning: TRIES evaluations against a frozen copy."""
    cand = rng.random((TRIES // 2, D))
    if seed_th is not None: cand = np.vstack([cand, seed_th])
    best = cand[dfn.exp_profit(s, cand).argmax()]
    local = np.clip(best + rng.normal(0, 0.05, (TRIES // 2, D)), 0, 1)
    allc = np.vstack([cand, local])
    e = dfn.exp_profit(s, allc)
    return allc[e.argmax()], e.max()


INFORMED = os.environ.get("INIT", "informed") == "informed"

def fresh_theta(rng, world, s):
    """A new attacker's starting numbers. 'informed': near a profitable spot on its
    shelf (attackers know what pays, not what we block); 'random': uniform."""
    if not INFORMED: return rng.random(D)
    return np.clip(world.peaks[s, rng.integers(3)] + rng.normal(0, 0.1, D), 0, 1)


def mutate(rng, s, th, style_hop):
    st, a, sd = unshelf(s)
    if rng.random() < 0.1: a = int(np.clip(a + rng.choice([-1, 1]), 0, 2))
    if rng.random() < 0.05: sd = 1 - sd
    if style_hop and rng.random() < 0.05: st = rng.integers(4)
    return shelf_id(st, a, sd), np.clip(th + rng.normal(0, 0.08, D), 0, 1)


# ---------------------------------------------------------------- conditions
class Swarm:
    """18 black-box attackers; keep top half by realized profit, mutate to refill."""
    def __init__(self, rng, world, dfn):
        self.rng, self.w, self.d = rng, world, dfn
        self.pop = [(s, fresh_theta(rng, world, s)) for s in PERSONAS for _ in range(3)]

    def round(self):
        res = [run_attempts(self.rng, self.w, self.d, s, th) for s, th in self.pop]
        order = np.argsort([-p for _, p in res])[:9]
        survivors = [self.pop[i] for i in order]
        self.pop = survivors + [mutate(self.rng, s, th, True) for s, th in survivors]
        return [r for r, _ in res]


class Lineages:
    """6 outsider personas: hill-climb their own playbook on outcome-only feedback."""
    def __init__(self, rng, world):
        self.rng = rng
        self.cur = [(s, fresh_theta(rng, world, s)) for s in PERSONAS]
        self.best = [(s, th, -np.inf) for s, th in self.cur]

    def feedback(self, i, profit):
        s, th = self.cur[i]
        if profit > self.best[i][2]: self.best[i] = (s, th, profit)
        bs, bth, _ = self.best[i]
        self.cur[i] = mutate(self.rng, bs, bth, False)


class Arena:
    """Shelf archive + learnability; spawners are gray-box (sandbox) or black-box."""
    def __init__(self, rng, world, dfn, graybox=True, shelves=True):
        self.rng, self.w, self.d, self.gray, self.shelves = rng, world, dfn, graybox, shelves
        self.arch = {s: [] for s in range(S)}   # entries: [theta, outsider, score]
        self.league = []                        # (s, theta, outsider, score) when no shelves
        self.lin = Lineages(rng, world)
        self.stats = np.zeros((3, S, 2))        # last 3 rounds: caught, attempts

    def learnability(self):
        c, a = self.stats[..., 0].sum(0), self.stats[..., 1].sum(0)
        cr = (c + 1) / (a + 2)
        return cr * (1 - cr), a < 20

    def insert(self, s, th, outsider, score):
        if not self.shelves:
            self.league.append((s, th, outsider, score)); return
        shelf = self.arch[s]
        if any(np.linalg.norm(th - e[0]) < 0.05 for e in shelf): return  # duplicate check
        shelf.append([th, outsider, score])
        outs = sorted([e for e in shelf if e[1]], key=lambda e: -e[2])
        reserved = outs[:1]
        rest = sorted([e for e in shelf if e is not (reserved[0] if reserved else None)], key=lambda e: -e[2])
        self.arch[s] = reserved + rest[:3 - len(reserved)]

    def members(self):
        if not self.shelves: return [(s, th, o) for s, th, o, _ in self.league]
        return [(s, e[0], e[1]) for s in range(S) for e in self.arch[s]]

    def pick_replay(self, n):
        if not self.shelves:  # plain league: run the current top scorers
            top = sorted(self.league, key=lambda e: -e[3])[:n]
            return [(s, th, o) for s, th, o, _ in top]
        mem = self.members()
        if not mem: return []
        L, _ = self.learnability()
        w = np.array([L[s] for s, _, _ in mem]) + 1e-9
        out = []
        for _ in range(n):
            if self.rng.random() < 0.8: i = self.rng.choice(len(mem), p=w / w.sum())
            else: i = self.rng.integers(len(mem))
            out.append(mem[i])
        return out

    def spawn_targets(self, n):
        if not self.shelves:
            seeds = sorted(self.league, key=lambda e: -e[3])[:n]
            return [(s, th) for s, th, _, _ in seeds] + [(None, None)] * (n - len(seeds))
        L, unknown = self.learnability()
        empty = np.array([len(self.arch[s]) == 0 for s in range(S)])
        explore = np.nonzero(unknown | empty)[0]
        chosen = []
        for _ in range(n):
            if len(explore) and self.rng.random() < 0.3:
                pool = [s for s in explore if s not in chosen]
            else:
                pool = []
            if not pool:
                pool = [s for s in np.argsort(-L) if s not in chosen][:1]
            s = int(self.rng.choice(pool)); chosen.append(s)
        out = []
        for s in chosen:
            best = max(self.arch[s], key=lambda e: e[2]) if self.arch[s] else None
            out.append((s, best[0] if best else None))
        return out

    def round(self):
        log, ran = [], []
        for i, (s, th) in enumerate(self.lin.cur):  # outsider lineages
            r, p = run_attempts(self.rng, self.w, self.d, s, th)
            log.append(r); ran.append((s, th, True, p)); self.lin.feedback(i, p)
        for s, th in self.spawn_targets(6):          # spawned attackers
            if self.gray:
                if s is None:  # no shelves, empty league: search shelf + numbers jointly
                    cands = self.rng.choice(S, 6, replace=False)
                    opts = [tune(self.rng, self.d, ss) for ss in cands]
                    k = int(np.argmax([e for _, e in opts])); s, th = int(cands[k]), opts[k][0]
                th, _ = tune(self.rng, self.d, s, th)
            else:
                th = fresh_theta(self.rng, self.w, s) if th is None else mutate(self.rng, s, th, False)[1]
            r, p = run_attempts(self.rng, self.w, self.d, s, th)
            log.append(r); ran.append((s, th, False, p))
        for s, th, o in self.pick_replay(6):         # archive / league replay
            r, p = run_attempts(self.rng, self.w, self.d, s, th)
            log.append(r); ran.append((s, th, o, p))
        self.stats = np.roll(self.stats, 1, axis=0); self.stats[0] = 0
        for s, th_n, caught, _ in log:
            self.stats[0, s] += [caught.sum(), len(caught)]
        for s, th, o, p in ran: self.insert(s, th, o, p)
        return log

    def rescore(self):  # after the defense learns: no stale scores
        if not self.shelves:
            self.league = [(s, th, o, float(self.d.exp_profit(s, th[None])[0])) for s, th, o, _ in self.league]
            self.league = sorted(self.league, key=lambda e: -e[3])[:72]
            return
        for s in range(S):
            for e in self.arch[s]: e[2] = float(self.d.exp_profit(s, e[0][None])[0])


# ------------------------------------------------------------------- metrics
def exam(rng, world, dfn, ref):
    best = np.array([tune(rng, dfn, s)[1] for s in range(S)])
    return best.max(), best.mean(), int((best < 0.25 * ref).sum())


def heldout_static(world, dfn, ho):
    return float(np.mean([dfn.exp_profit(s, th[None])[0] for s, th in ho]))


def heldout_catch(dfn, ho):
    return float(np.mean([dfn.p_catch(s, th[None])[0] for s, th in ho]))


def heldout_swarm(rng, world, dfn, gens=15):
    sw = Swarm(rng, world, dfn); best = -np.inf
    for _ in range(gens):
        sw.round()
        best = max(best, max(dfn.exp_profit(s, th[None])[0] for s, th in sw.pop))
    return best


CONDITIONS = {
    "swarm": lambda rng, w, d: Swarm(rng, w, d),
    "swarm+shelves": lambda rng, w, d: Arena(rng, w, d, graybox=False, shelves=True),
    "tagteam-noshelf": lambda rng, w, d: Arena(rng, w, d, graybox=True, shelves=False),
    "tagteam-arena": lambda rng, w, d: Arena(rng, w, d, graybox=True, shelves=True),
}


def run(cond, seed):
    world = World(np.random.default_rng(seed))
    ho_rng = np.random.default_rng(10_000 + seed)
    ho = []
    for _ in range(120):
        s = int(ho_rng.integers(S)); k = ho_rng.integers(3)
        ho.append((s, np.clip(world.peaks[s, k] + ho_rng.normal(0, 0.1, D), 0, 1)))
    rng = np.random.default_rng(seed * 7 + list(CONDITIONS).index(cond) * 1000)
    dfn = Defense(world)
    ref = np.array([tune(np.random.default_rng(seed), dfn, s)[1] for s in range(S)])  # no learned rules
    trainer = CONDITIONS[cond](rng, world, dfn)
    exam_rng = np.random.default_rng(20_000 + seed)  # same exam stream for every condition
    curve = []
    for _ in range(ROUNDS):
        log = trainer.round()
        train_catch = np.mean([c.mean() for _, _, c, _ in log])  # vs this round's own attackers
        dfn.learn(log)
        if hasattr(trainer, "rescore"): trainer.rescore()
        curve.append(exam(exam_rng, world, dfn, ref) + (heldout_static(world, dfn, ho), train_catch, heldout_catch(dfn, ho)))
    hs = heldout_swarm(np.random.default_rng(30_000 + seed), world, dfn)
    return np.array(curve), hs


if __name__ == "__main__":
    import json, sys
    out = {}
    for cond in CONDITIONS:
        curves, hss = [], []
        for seed in range(SEEDS):
            c, hs = run(cond, seed)
            curves.append(c); hss.append(hs)
        curves = np.array(curves)  # seeds x rounds x metrics
        out[cond] = {"curves": curves.tolist(), "heldout_swarm": hss}
        f = curves[:, -1]
        print(f"{cond:16s} exploit_max={f[:,0].mean():.2f}±{f[:,0].std():.2f} "
              f"exploit_mean={f[:,1].mean():.2f}±{f[:,1].std():.2f} "
              f"defended={f[:,2].mean():.1f}/24 heldout_static={f[:,3].mean():.3f} "
              f"heldout_swarm={np.mean(hss):.2f}±{np.std(hss):.2f}", flush=True)
    json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else "sim/results.json", "w"))
