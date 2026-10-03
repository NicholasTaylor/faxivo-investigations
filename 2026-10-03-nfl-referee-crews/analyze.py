"""Analysis for 'Do NFL referee crews move betting outcomes? (2015-2025)'.
Reads only from data/raw/. Prints every number used in the article. Follows the
pre-registered plan in README.md. Seeded for reproducibility.
"""
import os, sys, warnings
import numpy as np, pandas as pd
from scipy import stats
import statsmodels.formula.api as smf
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import proportion_confint

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw")
OUT = os.path.join(HERE, "data", "output"); os.makedirs(OUT, exist_ok=True)
B = int(os.environ.get("NPERM", 10000))
rng = np.random.default_rng(20261003)
SEASONS = list(range(2015, 2026))
TEAMMAP = {"OAK": "LV", "SD": "LAC", "STL": "LA"}

def H(t): print("\n=== " + t + " ===")

# ---------------------------------------------------------------- load
off = pd.read_parquet(os.path.join(RAW, "officials.parquet"))
refs = off[off.position == "Referee"][["game_id", "official_name", "season", "season_type"]] \
    .rename(columns={"game_id": "old_game_id", "official_name": "ref"})
g = pd.read_parquet(os.path.join(RAW, "games.parquet"))
g = g[(g.game_type == "REG") & g.season.isin(SEASONS) & g.result.notna()].copy()
for c in ("home_team", "away_team"): g[c] = g[c].replace(TEAMMAP)
g = g.merge(refs[["old_game_id", "ref"]], on="old_game_id", how="left")
H("coverage")
print("regular-season games 2015-2025 in schedules:", len(g))
print("games without a referee in officials file:", g.ref.isna().sum())
# Reconciliation (added after adversarial review): where officials.parquet and the schedules
# `referee` column disagree, or officials has none, use ESPN's listed referee; drop if ESPN lists none.
chk = pd.read_csv(os.path.join(RAW, "espn_referee_checks.csv"), dtype={"old_game_id": str})
print("games where officials file and schedules disagree or officials missing:", len(chk),
      "; resolved by ESPN:", chk.espn_ref.notna().sum(), "; dropped (ESPN lists no referee):", chk.espn_ref.isna().sum())
changed = chk[chk.espn_ref.notna() & (chk.espn_ref != chk.officials_ref)]
print("referee labels changed from officials file:", len(changed), "; of which officials file had none:", changed.officials_ref.isna().sum())
fix = chk.set_index("old_game_id").espn_ref
m = g.old_game_id.isin(fix.index)
g.loc[m, "ref"] = g.loc[m, "old_game_id"].map(fix)
g = g[g.ref.notna()].copy()
REFS25 = sorted(off[(off.season == 2025) & (off.season_type == "REG") & (off.position == "Referee")].official_name.unique())
print("referees leading crews in 2025 REG:", len(REFS25), REFS25)
g["ref17"] = np.where(g.ref.isin(REFS25), g.ref, "_other")
print("games in analysis:", len(g), "; games refereed by the 17:", (g.ref17 != "_other").sum())
print(g[g.ref17 != "_other"].groupby("ref17").agg(games=("game_id", "size"), first=("season", "min"),
      seasons=("season", "nunique")).to_string())

# ---------------------------------------------------------------- game metrics
def implied(ml):
    ml = ml.astype(float)
    return np.where(ml > 0, 100 / (ml + 100), -ml / (-ml + 100))
ph, pa = implied(g.home_moneyline), implied(g.away_moneyline)
g["p_home"] = ph / (ph + pa)
g["home_win"] = np.where(g.result > 0, 1.0, np.where(g.result < 0, 0.0, 0.5))
g["total_resid"] = g.total - g.total_line
g["over"] = np.where(g.total > g.total_line, 1.0, np.where(g.total < g.total_line, 0.0, np.nan))
g["home_ats"] = g.result - g.spread_line
g["home_cover"] = np.where(g.home_ats > 0, 1.0, np.where(g.home_ats < 0, 0.0, np.nan))
g["home_ml_resid"] = g.home_win - g.p_home

pen = pd.read_parquet(os.path.join(RAW, "penalties_2015_2025.parquet"))
pen["penalty_yards"] = pen.penalty_yards.abs()
idx = pd.read_parquet(os.path.join(RAW, "pbp_game_index.parquet"))
print("schedule games with no pbp:", (~g.old_game_id.isin(idx.old_game_id)).sum())
g = g[g.old_game_id.isin(idx.old_game_id)].copy()
pg = pen.groupby("old_game_id").agg(nflags=("play_id", "size"), pen_yards=("penalty_yards", "sum"))
g = g.join(pg, on="old_game_id"); g[["nflags", "pen_yards"]] = g[["nflags", "pen_yards"]].fillna(0)
H("league context")
print(f"games={len(g)} mean flags/game={g.nflags.mean():.2f} mean pen yards/game={g.pen_yards.mean():.1f}")
print(f"over rate (ex pushes)={g.over.mean():.4f} n={g.over.notna().sum()}; home cover={g.home_cover.mean():.4f} n={g.home_cover.notna().sum()}")
print("missing lines:", g[["spread_line", "total_line", "p_home"]].isna().sum().to_dict())

# position groups
POS = {"QB": "QB", "RB": "RB", "FB": "RB", "HB": "RB", "WR": "WR", "TE": "TE",
       "T": "OL", "G": "OL", "C": "OL", "OT": "OL", "OG": "OL", "OL": "OL",
       "DE": "DL", "DT": "DL", "NT": "DL", "DL": "DL",
       "OLB": "LB", "ILB": "LB", "MLB": "LB", "LB": "LB",
       "CB": "DB", "S": "DB", "SS": "DB", "FS": "DB", "DB": "DB", "SAF": "DB",
       "K": "ST", "P": "ST", "LS": "ST"}
ros = pd.concat([pd.read_parquet(os.path.join(RAW, f"roster_{s}.parquet"), columns=["season", "gsis_id", "position"]) for s in SEASONS])
ros["grp"] = ros.position.map(POS)
ros = ros.dropna(subset=["gsis_id", "grp"]).groupby(["season", "gsis_id"]).grp.agg(lambda s: s.mode().iat[0]).reset_index()
pen = pen.merge(ros, left_on=["season", "penalty_player_id"], right_on=["season", "gsis_id"], how="left")
pen["grp"] = pen.grp.fillna("unattributed")
GROUPS = ["QB", "RB", "WR", "TE", "OL", "DL", "LB", "DB", "ST"]
H("position-group attribution")
print(pen.grp.value_counts().to_string())
print(f"attributed share: {(pen.grp != 'unattributed').mean():.4f} of {len(pen)} flags")
gp = pen.pivot_table(index="old_game_id", columns="grp", values="play_id", aggfunc="size", fill_value=0)
for c in GROUPS: g["grp_" + c] = g.old_game_id.map(gp[c]).fillna(0) if c in gp else 0

# team-game table
rows = []
for side, opp in (("home", "away"), ("away", "home")):
    t = g[["game_id", "old_game_id", "season", "ref", "ref17", "total_resid", "p_home", "home_win", "result", "spread_line"]].copy()
    t["team"] = g[f"{side}_team"]; t["is_home"] = side == "home"
    sgn = 1 if side == "home" else -1
    t["ats"] = sgn * (g.result - g.spread_line)
    t["win"] = g.home_win if side == "home" else 1 - g.home_win
    t["p_win"] = g.p_home if side == "home" else 1 - g.p_home
    rows.append(t)
tg = pd.concat(rows, ignore_index=True)
tg["cover"] = np.where(tg.ats > 0, 1.0, np.where(tg.ats < 0, 0.0, np.nan))
tg["ml_resid"] = tg.win - tg.p_win
pt = pen.groupby(["old_game_id", "penalty_team"]).agg(t_flags=("play_id", "size"), t_yards=("penalty_yards", "sum")).reset_index()
tg = tg.merge(pt, left_on=["old_game_id", "team"], right_on=["old_game_id", "penalty_team"], how="left")
tg[["t_flags", "t_yards"]] = tg[["t_flags", "t_yards"]].fillna(0)
assert len(tg) == 2 * len(g)
assert abs(tg.t_flags.sum() - g.nflags.sum()) < 1e-9, "team flags must add to game flags"
for c in ("t_flags", "t_yards"):  # remove league home/away x season effect
    tg[c + "_adj"] = tg[c] - tg.groupby(["season", "is_home"])[c].transform("mean")

# ---------------------------------------------------------------- permutation machinery
def perm_test(df, value, label, strata, B=B, min_n=1):
    """Two-sided permutation test of mean(value - stratum mean) for each label,
    shuffling labels within strata. Returns DataFrame label,n,stat,p."""
    d = df[[value, label, strata]].dropna(subset=[value]).reset_index(drop=True)
    x = d[value].to_numpy(float) - d.groupby(strata)[value].transform("mean").to_numpy(float)
    labs, lab_idx = np.unique(d[label].to_numpy(), return_inverse=True)
    L = len(labs)
    n = np.bincount(lab_idx, minlength=L)
    obs = np.bincount(lab_idx, weights=x, minlength=L) / np.maximum(n, 1)
    s_codes = pd.factorize(d[strata])[0]
    order = np.argsort(s_codes, kind="stable")
    s_sorted = s_codes[order]
    x_sorted = x[order]; lab_sorted = lab_idx[order]
    exceed = np.zeros(L)
    for _ in range(B):
        key = s_sorted + rng.random(len(s_sorted))
        perm = np.argsort(key, kind="stable")  # permutation within each stratum block
        pl = lab_sorted[perm]
        st = np.bincount(pl, weights=x_sorted, minlength=L) / np.maximum(n, 1)
        exceed += np.abs(st) >= np.abs(obs) - 1e-12
    p = (1 + exceed) / (1 + B)
    # Effect as pre-registered: label's mean minus the mean of OTHER labels' rows in the same strata
    # (x is already stratum-demeaned). 95% CI from the two-sample normal approximation.
    diff = np.full(L, np.nan); se = np.full(L, np.nan)
    for li in range(L):
        if n[li] < max(min_n, 2): continue
        own = lab_idx == li
        others = np.isin(s_codes, np.unique(s_codes[own])) & ~own
        a, b = x[own], x[others]
        diff[li] = a.mean() - b.mean()
        se[li] = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    res = pd.DataFrame({"label": labs, "n": n, "stat": diff, "dev_from_stratum_mean": obs, "se": se, "p": p})
    res["lo"] = res.stat - 1.96 * res.se; res["hi"] = res.stat + 1.96 * res.se
    return res[res.n >= min_n]

def bh(res):
    res = res.copy()
    rej, q, _, _ = multipletests(res.p, alpha=0.05, method="fdr_bh")
    res["q"] = q; res["bh_sig"] = rej
    return res

def family_summary(name, res, show=5, fmt="{:+.3f}"):
    k = len(res); raw = (res.p < 0.05).sum(); bhn = res.bh_sig.sum()
    print(f"[{name}] tests={k} raw p<0.05={raw} expected by chance={0.05*k:.1f} BH-significant(q<0.05)={bhn} min p={res.p.min():.4f} min q={res.q.min():.3f}")
    top = res.sort_values("p").head(show)
    for _, r in top.iterrows():
        print(f"    {r.label:<32} n={int(r.n):>4} effect={fmt.format(r.stat)} 95%CI[{fmt.format(r.lo)},{fmt.format(r.hi)}] p={r.p:.4f} q={r.q:.3f}")

# ---------------------------------------------------------------- crew level
H("crew level (17 referees vs other crews in the same seasons; permutation within season)")
crew_metrics = {"C1 total points minus total line": "total_resid", "C1b over rate (ex pushes)": "over",
                "C2 home margin minus spread": "home_ats", "C2b home cover rate (ex pushes)": "home_cover",
                "C3 home win minus implied prob": "home_ml_resid",
                "C4 accepted flags per game": "nflags", "C5 penalty yards per game": "pen_yards"}
crew_res = {}
for name, col in crew_metrics.items():
    r = perm_test(g, col, "ref17", "season")
    r = bh(r[r.label != "_other"])
    crew_res[col] = r
    family_summary(name, r, show=17 if col in ("nflags", "total_resid", "over") else 4)
    r.assign(metric=col).to_csv(os.path.join(OUT, f"crew_{col}.csv"), index=False)

f = crew_res["nflags"]
print(f"flags/game: range of crew effects {f.stat.min():+.2f} to {f.stat.max():+.2f}; BH-significant crews {f.bh_sig.sum()} of {len(f)}")
for _, r in f.sort_values("stat").iterrows():
    m = g[g.ref17 == r.label]
    print(f"    {r.label:<18} games={int(r.n):>3} flags/game={m.nflags.mean():5.2f} vs other crews {r.stat:+.2f}  over {int((m.over==1).sum())}-{int((m.over==0).sum())}-{int(m.over.isna().sum())} ({m.over.mean():.3f}) total-line resid {m.total_resid.mean():+.2f}")

H("does crew flag rate relate to totals? (17 refs)")
t = crew_res["total_resid"].set_index("label").stat; fl = f.set_index("label").stat
o = crew_res["over"].set_index("label").stat
print("pearson(flags effect, total_resid effect) r=%.3f p=%.3f" % stats.pearsonr(fl, t.loc[fl.index]))
print("spearman(flags effect, over effect) rho=%.3f p=%.3f" % stats.spearmanr(fl, o.loc[fl.index]))
print("game-level: corr(flags, total_resid) r=%.3f p=%.2g n=%d" % (*stats.pearsonr(g.nflags, g.total_resid), len(g)))

# variance components
H("variance components: between-crew SD (MixedLM, 17 refs' games, season fixed effects)")
g17 = g[g.ref17 != "_other"].copy()  # used again in power section
for col in ("total_resid", "home_ats", "home_ml_resid", "nflags", "pen_yards", "over"):
    d = g17.dropna(subset=[col])
    m1 = smf.mixedlm(f"{col} ~ C(season)", d, groups=d["ref17"]).fit(reml=False)
    m0 = smf.ols(f"{col} ~ C(season)", d).fit()
    lr = max(0.0, 2 * (m1.llf - m0.llf)); pv = 0.5 * stats.chi2.sf(lr, 1) if lr > 0 else 1.0
    sd_between = float(np.sqrt(max(m1.cov_re.iloc[0, 0], 0)))
    print(f"{col:<14} between-crew SD={sd_between:.3f} residual SD={np.sqrt(m1.scale):.3f} LRT p={pv:.3g} n={len(d)}")

# ---------------------------------------------------------------- crew x position group
H("crew x position group: flags/game against each group (permutation within season)")
grp_all = []
for G in GROUPS:
    r = perm_test(g, "grp_" + G, "ref17", "season")
    r = r[r.label != "_other"].assign(group=G)
    grp_all.append(r)
grp_all = pd.concat(grp_all, ignore_index=True)
grp_all["label"] = grp_all.label + " x " + grp_all.group
grp_all = bh(grp_all)
family_summary("crew x position group flags/game", grp_all, show=12, fmt="{:+.3f}")
grp_all.to_csv(os.path.join(OUT, "crew_x_group.csv"), index=False)
print("league mean flags/game by group:", {G: round(g["grp_" + G].mean(), 3) for G in GROUPS})

# ---------------------------------------------------------------- crew x team
H("crew x team (cells with >=3 games; permutation within team-season)")
tg["cell"] = tg.ref17 + " x " + tg.team
tg["team_season"] = tg.team + "_" + tg.season.astype(str)
team_metrics = {"T1 flags against team": "t_flags_adj", "T2 penalty yards against team": "t_yards_adj",
                "T3 team margin minus spread": "ats", "T3b team cover rate": "cover",
                "T4 total points minus total line": "total_resid", "T5 team win minus implied prob": "ml_resid"}
cell_res = {}
for name, col in team_metrics.items():
    r = perm_test(tg, col, "cell", "team_season", min_n=3)
    r = bh(r[~r.label.str.startswith("_other")])
    cell_res[col] = r
    family_summary(name, r, show=5)
    r.to_csv(os.path.join(OUT, f"cell_{col}.csv"), index=False)
ncell = tg[tg.ref17 != "_other"].groupby("cell").size()
print(f"crew x team cells: {len(ncell)} with any game; >=3 games: {(ncell>=3).sum()}; median games/cell {ncell.median():.0f}; max {ncell.max()}")

H("crew x team: random-effects between-cell SD (MixedLM on team-season-demeaned values, 17 refs)")
t17 = tg[tg.ref17 != "_other"].copy()
for col in ("t_flags_adj", "ats", "cover", "total_resid", "ml_resid"):
    d = t17.dropna(subset=[col]).copy()
    d["y"] = d[col] - tg.dropna(subset=[col]).groupby("team_season")[col].transform("mean").reindex(d.index)
    m1 = smf.mixedlm("y ~ 1", d, groups=d["cell"]).fit(reml=False)
    m0 = smf.ols("y ~ 1", d).fit()
    lr = max(0.0, 2 * (m1.llf - m0.llf)); pv = 0.5 * stats.chi2.sf(lr, 1) if lr > 0 else 1.0
    sdb = float(np.sqrt(max(m1.cov_re.iloc[0, 0], 0)))
    # shrunken estimate of the most extreme raw cell
    rr = cell_res[col]; top = rr.iloc[rr.stat.abs().argmax()]
    shrink = sdb**2 / (sdb**2 + m1.scale / top.n) if sdb > 0 else 0.0
    print(f"{col:<12} between-cell SD={sdb:.3f} residual SD={np.sqrt(m1.scale):.3f} LRT p={pv:.3g}; most extreme raw cell {top.label} n={int(top.n)} raw={top.stat:+.3f} shrunk={top.stat*shrink:+.3f}")

# Named examples: cover records of the top raw cells, for context
H("most extreme crew x team cover records (raw, before correction)")
cv = cell_res["cover"].sort_values("p").head(5)
for _, r in cv.iterrows():
    d = tg[(tg.cell == r.label) & tg.cover.notna()]
    print(f"    {r.label:<28} {int(d.cover.sum())}-{int((d.cover==0).sum())} p={r.p:.4f} q={r.q:.3f}")

# ---------------------------------------------------------------- out of sample
H("out of sample: split 2015-2021 (fit) vs 2022-2025 (test)")
early, late = g[g.season <= 2021], g[g.season >= 2022]
def crew_means(d, col):
    d = d.dropna(subset=[col]).copy()
    d["x"] = d[col] - d.groupby("season")[col].transform("mean")
    s = d[d.ref17 != "_other"].groupby("ref17").x.agg(["mean", "size"])
    return s
for col in ("nflags", "pen_yards", "total_resid", "over", "home_ats", "home_cover", "home_ml_resid"):
    a, b = crew_means(early, col), crew_means(late, col)
    j = a.join(b, lsuffix="_e", rsuffix="_l").dropna()
    j = j[(j.size_e >= 30) & (j.size_l >= 30)]
    r, p = stats.pearsonr(j.mean_e, j.mean_l)
    print(f"{col:<14} refs={len(j)} pearson r={r:+.3f} p={p:.3f}")
for _, grp_ in [(0, G) for G in GROUPS]:
    a, b = crew_means(early, "grp_" + grp_), crew_means(late, "grp_" + grp_)
    j = a.join(b, lsuffix="_e", rsuffix="_l").dropna(); j = j[(j.size_e >= 30) & (j.size_l >= 30)]
    r, p = stats.pearsonr(j.mean_e, j.mean_l)
    print(f"   group {grp_:<3} flags/game refs={len(j)} r={r:+.3f} p={p:.3f}")

def cell_means(d, col):
    d = d.dropna(subset=[col]).copy()
    d["x"] = d[col] - d.groupby("team_season")[col].transform("mean")
    return d[d.ref17 != "_other"].groupby("cell").x.agg(["mean", "size"])
te, tl = tg[tg.season <= 2021], tg[tg.season >= 2022]
for col in ("t_flags_adj", "t_yards_adj", "ats", "cover", "total_resid", "ml_resid"):
    j = cell_means(te, col).join(cell_means(tl, col), lsuffix="_e", rsuffix="_l").dropna()
    j = j[(j.size_e >= 3) & (j.size_l >= 3)]
    r, p = stats.pearsonr(j.mean_e, j.mean_l)
    print(f"cell {col:<12} cells={len(j)} pearson r={r:+.3f} p={p:.3f}")

# ---------------------------------------------------------------- walk-forward betting simulation
H("walk-forward betting simulation 2019-2025 (signals from all prior seasons since 2015)")
def wilson(k, n):
    lo, hi = proportion_confint(k, n, method="wilson"); return lo, hi
def report(name, w, l):
    n = w + l
    if n == 0: print(f"{name}: no bets"); return
    lo, hi = wilson(w, n)
    units = w * (100 / 110) - l
    print(f"{name}: {w}-{l} win rate={w/n:.4f} 95%CI[{lo:.4f},{hi:.4f}] (break-even 0.5238) units at -110={units:+.1f}")

wf_over = [0, 0]; wf_home = [0, 0]; wf_cell = [0, 0]; nsig_over = []; per_season = []; cell_bets = []
for S in range(2019, 2026):
    prior, cur = g[g.season < S], g[g.season == S]
    sw = [0, 0]
    for ref, d in prior[prior.ref17 != "_other"].groupby("ref17"):
        o = d.over.dropna(); k, n = int(o.sum()), len(o)
        if n == 0: continue
        if stats.binomtest(k, n, 0.5).pvalue < 0.05:
            nsig_over.append((S, ref, k, n))
            side = 1.0 if k / n > 0.5 else 0.0
            c = cur[(cur.ref17 == ref)].over.dropna()
            w = int((c == side).sum()); l = int((c != side).sum())
            wf_over[0] += w; wf_over[1] += l; sw[0] += w; sw[1] += l
        hc = d.home_cover.dropna(); k2, n2 = int(hc.sum()), len(hc)
        if n2 and stats.binomtest(k2, n2, 0.5).pvalue < 0.05:
            side = 1.0 if k2 / n2 > 0.5 else 0.0
            c = cur[cur.ref17 == ref].home_cover.dropna()
            wf_home[0] += int((c == side).sum()); wf_home[1] += int((c != side).sum())
    per_season.append((S, *sw))
    tp, tc = tg[tg.season < S], tg[tg.season == S]
    cs = tp[tp.ref17 != "_other"].dropna(subset=["cover"]).groupby("cell").cover.agg(["sum", "size"])
    for cell, r in cs[cs["size"] >= 4].iterrows():
        rate = r["sum"] / r["size"]
        if rate >= 0.75 or rate <= 0.25:
            side = 1.0 if rate >= 0.75 else 0.0
            c = tc[tc.cell == cell].dropna(subset=["cover"])
            wf_cell[0] += int((c.cover == side).sum()); wf_cell[1] += int((c.cover != side).sum())
            for _, b in c.iterrows():  # record bet as "home covers" (1) / "away covers" (0) for de-duplication
                home_side = side if b.is_home else 1 - side
                cell_bets.append((b.game_id, home_side, float(b.cover == side)))
print("crew-level over/under signals (season, ref, overs, decided games):", nsig_over)
report("Crew-level totals (pre-registered)", *wf_over)
print("   per season (S, wins, losses):", per_season)
report("Crew x team spread (pre-registered)", *wf_cell)
cb = pd.DataFrame(cell_bets, columns=["game_id", "home_side", "won"])
per_game = cb.groupby("game_id").agg(nb=("won", "size"), sides=("home_side", "nunique"), won=("won", "first"))
print(f"   crew x team bets={len(cb)} on {len(per_game)} games; games bet more than once={int((per_game.nb>1).sum())} "
      f"(bets on both sides of the same game: {int((per_game.sides>1).sum())})")
dedup = per_game[per_game.sides == 1]
report("Crew x team spread, one bet per game (opposing bets cancelled)", int(dedup.won.sum()), int((dedup.won == 0).sum()))
report("Crew-level home spread (added, not pre-registered)", *wf_home)

# ---------------------------------------------------------------- chance benchmark for crew x team
H("chance benchmark")
for col, r in cell_res.items():
    print(f"{col:<12} cells={len(r)} raw p<0.05={int((r.p<0.05).sum())} expected={0.05*len(r):.1f} BH={int(r.bh_sig.sum())}")
print("total crew x team tests across T1-T5 families:", sum(len(r) for r in cell_res.values()))

# ---------------------------------------------------------------- exploratory (NOT pre-registered)
H("EXPLORATORY (not pre-registered)")
fl_ex = fl.drop("Alex Moore"); t_ex = t.loc[fl_ex.index]
print("crew flags effect vs total_resid effect, excluding Alex Moore (16 games): r=%.3f p=%.3f n=%d" % (*stats.pearsonr(fl_ex, t_ex), len(fl_ex)))
# season-to-season persistence of crew-level effects (ref-season means vs same ref next season)
ys = []
for col in ("nflags", "pen_yards", "total_resid", "over", "home_ats"):
    d = g.dropna(subset=[col]).copy(); d["x"] = d[col] - d.groupby("season")[col].transform("mean")
    rs = d.groupby(["ref", "season"]).x.mean().reset_index()
    nxt = rs.assign(season=rs.season - 1)
    j = rs.merge(nxt, on=["ref", "season"], suffixes=("", "_next"))
    r, p = stats.pearsonr(j.x, j.x_next)
    print(f"year-to-year {col:<12} ref-season pairs={len(j)} (all referees) r={r:+.3f} p={p:.3g}")

# ---------------------------------------------------------------- power and upper bounds (added after review)
H("power: minimum detectable effects (80% power, two-sided alpha=0.05) and upper bounds on crew effects")
from scipy.stats import norm as N
za, zb = N.ppf(0.975), N.ppf(0.80); za_bh = N.ppf(1 - 0.05 / 17 / 2)
sd_tot = g.total_resid.std()
for n_ in (170, 110):
    print(f"n={n_} games: over-rate MDE=+/-{(za+zb)*0.5/np.sqrt(n_)*100:.1f} pp (at alpha=0.05/17: {(za_bh+zb)*0.5/np.sqrt(n_)*100:.1f} pp); "
          f"total-residual MDE=+/-{(za+zb)*sd_tot/np.sqrt(n_):.1f} pts (at 0.05/17: {(za_bh+zb)*sd_tot/np.sqrt(n_):.1f})")
print(f"game-level SD of total minus line={sd_tot:.2f}; break-even at -110 is 52.38% (+2.38 pp over 50%)")
print(f"games per crew needed to detect 52.38% vs 50% (80% power, alpha=.05): {int(np.ceil(((za+zb)*0.5/0.0238)**2))}")

def q_profile_upper(y, v, level=0.95):
    """Upper bound of between-crew SD (tau) by the Q-profile method (Viechtbauer 2007)."""
    k = len(y); target = stats.chi2.ppf((1 - level) / 2, k - 1)
    def Q(t2):
        w = 1 / (v + t2); mu = np.sum(w * y) / np.sum(w); return np.sum(w * (y - mu) ** 2)
    if Q(0) < target: return 0.0, Q(0)
    lo, hi = 0.0, 100 * np.var(y) + 1
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if Q(mid) > target else (lo, mid)
    return np.sqrt(hi), Q(0)
for col, unit in (("over", "pp"), ("total_resid", "pts"), ("home_cover", "pp"), ("home_ats", "pts"), ("nflags", "flags"), ("total", "pts")):
    d = g17.dropna(subset=[col]).copy(); d["x"] = d[col] - g.dropna(subset=[col]).groupby("season")[col].transform("mean").reindex(d.index)
    a = d.groupby("ref17").x.agg(["mean", "var", "size"])
    up, q0 = q_profile_upper(a["mean"].to_numpy(), (a["var"] / a["size"]).to_numpy())
    mult = 100 if unit == "pp" else 1
    print(f"{col:<12} Q(0)={q0:.1f} on df={len(a)-1}; 95% upper bound on between-crew SD = {up*mult:.2f} {unit}")

H("raw scoring by crew (total points, not relative to the line; added after review)")
r = bh(perm_test(g, "total", "ref17", "season").query("label != '_other'"))
family_summary("total points per game (raw)", r, show=4)

H("penalty-count limitation: plays whose description mentions more than one penalty")
npen = pen.desc.str.count(r"PENALTY on")
print(f"penalty plays with >1 'PENALTY on' in description: {(npen>1).sum()} of {len(pen)}; extra penalty mentions: {int((npen-1).clip(lower=0).sum())} (includes declined/offsetting)")
