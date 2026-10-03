"""Analysis: how often is a vulnerability fix still inside a dependency-cooldown window when its advisory goes public?

Reads only from data/. Prints every number used in the article, labelled.
"""
import csv
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
import semver
from packaging.version import InvalidVersion, Version

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
WINDOW_START = pd.Timestamp("2022-01-01", tz="UTC")
WINDOW_END = pd.Timestamp("2026-10-01", tz="UTC")  # exclusive; i.e. through 2026-09-30
COOLDOWNS = [1, 3, 7, 14]
SEED = 20261003

# LLM / agent stack packages, fixed before looking at results (prefix match on the lowercased name).
AI_PYPI_PREFIXES = [
    "langchain", "langgraph", "langsmith", "llama-index", "llama_index", "llamaindex", "litellm", "openai",
    "anthropic", "transformers", "vllm", "sglang", "gradio", "mlflow", "open-webui", "langflow", "crewai",
    "pyautogen", "autogen", "ag2", "haystack-ai", "farm-haystack", "dspy", "semantic-kernel", "chainlit", "mcp",
    "fastmcp", "bentoml", "ray", "llama-cpp-python", "huggingface-hub", "guidance", "text-generation",
    "lmdeploy", "ollama", "agno", "phidata", "smolagents", "pydantic-ai", "instructor", "promptflow", "letta",
    "keras", "torch", "onnx", "tensorflow", "safetensors", "diffusers", "accelerate", "peft", "unsloth",
    "kserve", "nltk", "comfyui",
]
AI_NPM_PREFIXES = [
    "langchain", "@langchain/", "llamaindex", "@llamaindex/", "openai", "@anthropic-ai/", "ai", "@ai-sdk/",
    "@modelcontextprotocol/", "flowise", "n8n", "@n8n/", "@mastra/", "mastra", "@huggingface/", "ollama",
    "@google/genai", "@google/generative-ai", "@openai/", "@vercel/ai",
]


def is_ai(eco, name):
    n = name.lower()
    if eco == "PyPI":
        return any(n == p or n.startswith(p + "-") or n.startswith(p + "_") or n.startswith(p + "[") for p in AI_PYPI_PREFIXES)
    for p in AI_NPM_PREFIXES:
        if p.endswith("/"):
            if n.startswith(p):
                return True
        elif n == p or n.startswith(p + "-"):
            return True
    return False


def ts(s):
    if not isinstance(s, str) or not s:
        return pd.NaT
    return pd.Timestamp(s).tz_convert("UTC") if pd.Timestamp(s).tzinfo else pd.Timestamp(s, tz="UTC")


def vkey(eco, v):
    """Sort key for versions: (kind, key). npm: 2 = valid semver, 1 = PEP 440 fallback, 0 = unparseable."""
    if eco == "npm":
        try:
            return (2, semver.Version.parse(v.lstrip("v")))
        except ValueError:
            pass
    try:
        return (1, Version(v))
    except InvalidVersion:
        return (0, v)


def load_registry():
    reg = {}
    status = {}
    for eco in ["PyPI", "npm"]:
        p = os.path.join(DATA, "raw", "registry", f"{eco}.jsonl")
        if not os.path.exists(p):
            print(f"WARNING: no registry data for {eco}")
            continue
        with open(p) as f:
            for line in f:
                d = json.loads(line)
                reg[(eco, d["package"])] = d["times"]
                status[(eco, d["package"])] = d["status"]
    return reg, status


def lookup_time(eco, times, v):
    if v in times:
        return times[v]
    if eco == "npm" and ("v" + v) in times:
        return times["v" + v]
    if eco == "PyPI":
        try:
            target = Version(v)
        except InvalidVersion:
            return None
        for k, t in times.items():
            try:
                if Version(k) == target:
                    return t
            except InvalidVersion:
                continue
    return None


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def pct(n, d):
    return f"{n}/{d} = {100 * n / d:.1f}%" if d else f"{n}/0"


def load_repo_advisories():
    sample = [x.strip() for x in open(os.path.join(DATA, "repo_sample.txt")) if x.strip()]
    cpath = os.path.join(DATA, "repo_census.txt")
    census = [x.strip() for x in open(cpath) if x.strip()] if os.path.exists(cpath) else []
    fetched, pub, rstat, listed = set(), {}, {}, []
    for line in open(os.path.join(DATA, "raw", "repo_advisories.jsonl")):
        d = json.loads(line)
        rstat[d["repo"]] = d["status"]
        if d["status"] == "ok":
            fetched.add(d["repo"])
            for x in d["advisories"]:
                if x.get("ghsa_id") and x.get("published_at"):
                    pub[x["ghsa_id"].upper()] = ts(x["published_at"])
                    listed.append((d["repo"], x["ghsa_id"].upper(), ts(x["published_at"])))
    return sample, census, fetched, pub, rstat, listed


def public_clock(a, block):
    """Stratified estimate of the share of units whose fix was younger than C days at public disclosure.
    Strata: (1) GitHub-curated advisories: full population, disclosure = database/NVD time;
    (2) repository advisories in the census of the largest repos: full stratum;
    (3) other repository advisories: simple random sample of repos, ratio estimator, bootstrap over repos."""
    sample, census, fetched, pub, rstat, listed = load_repo_advisories()
    cset = set(census)
    print(f"  repo sample: {len(sample)} drawn; census: {len(census)} largest repos; fetched ok: {len(fetched)}; "
          f"status {pd.Series(rstat).value_counts().to_dict()}")
    a = a.copy()
    a["is_repo"] = a.repo != ""
    nonrepo = a[~a.is_repo]
    rep = a[a.is_repo].copy()

    def pub_time(r):
        t = [pub[g.upper()] for g in r.ghsa_ids.split(";") if g.upper() in pub]
        return min(t) if t else pd.NaT
    rep["P_repo"] = rep.apply(pub_time, axis=1)
    rep["P_pub"] = rep[["P", "P_repo"]].min(axis=1)
    rep["D_pub"] = (rep.P_pub - rep.F_hi).dt.total_seconds() / 86400
    rep["lag"] = (rep.P - rep.P_repo).dt.total_seconds() / 86400
    A = rep[rep.repo.isin(cset)]
    B_all = rep[~rep.repo.isin(cset)]
    B_s = B_all[B_all.repo.isin(set(sample)) & B_all.repo.isin(fetched)]
    print(f"  units: total {len(a)}; GitHub-curated {len(nonrepo)}; repository-advisory {len(rep)} in {rep.repo.nunique()} repos")
    print(f"  census stratum: {len(A)} units in {A.repo.nunique()} repos (fetched {len(cset & fetched)}/{len(cset)}); "
          f"maintainer time found for {int(A.P_repo.notna().sum())}")
    print(f"  sampled stratum: population {len(B_all)} units in {B_all.repo.nunique()} repos; sample {len(B_s)} units in "
          f"{B_s.repo.nunique()} repos; maintainer time found for {int(B_s.P_repo.notna().sum())}")
    obs = pd.concat([A, B_s])
    obs.drop(columns=["all_fix_times"]).to_csv(os.path.join(DATA, "repo_observed_units.csv"), index=False)

    print("  database entry minus maintainer publication (days), observed repo units:")
    for lab, g in [("2022-2025", obs[obs.P_pub < pd.Timestamp("2026-01-01", tz="UTC")]),
                   ("2026", obs[obs.P_pub >= pd.Timestamp("2026-01-01", tz="UTC")])]:
        l = g.lag.dropna()
        if len(l):
            print(f"    {lab}: n={len(l)} median={l.median():.2f} p75={l.quantile(.75):.2f} share>7d={pct(int((l > 7).sum()), len(l))}")
    hrs = obs.D_pub * 24
    print(f"  observed repo units: median D_pub = {hrs.median():.1f} h; PyPI {hrs[obs.ecosystem == 'PyPI'].median():.1f} h; "
          f"npm {hrs[obs.ecosystem == 'npm'].median():.1f} h")
    print(f"  observed repo units: maintainer advisory before fix upload (D_pub<0): {pct(int((obs.D_pub < 0).sum()), len(obs))}; "
          f"same day or earlier (D_pub<1): {pct(int((obs.D_pub < 1).sum()), len(obs))}")

    rng = np.random.default_rng(SEED)
    groups = {r_: g for r_, g in B_s.groupby("repo")}
    repos_b = sorted(groups)

    def stratified(filt, label, nboot=2000):
        n0, nA, nB = filt(nonrepo), filt(A), filt(B_all)
        Bs = filt(B_s)
        N = len(n0) + len(nA) + len(nB)
        if N == 0 or len(Bs) == 0:
            print(f"  -- {label}: insufficient data"); return
        boots = {c: [] for c in COOLDOWNS}
        for _ in range(nboot):
            pick = rng.choice(repos_b, size=len(repos_b), replace=True)
            bs = filt(pd.concat([groups[p] for p in pick]))
            if len(bs) == 0:
                continue
            for c in COOLDOWNS:
                boots[c].append((bs.D_pub < c).mean())
        print(f"  -- {label}: N={N} units (curated {len(n0)}, census {len(nA)}, sampled-stratum {len(nB)}; sample n={len(Bs)} in {Bs.repo.nunique()} repos) --")
        for c in COOLDOWNS:
            k0 = int((n0.D < c).sum())
            kA = int((nA.D_pub < c).sum())
            pB = (Bs.D_pub < c).mean()
            est = (k0 + kA + len(nB) * pB) / N
            lo = (k0 + kA + len(nB) * np.percentile(boots[c], 2.5)) / N
            hi = (k0 + kA + len(nB) * np.percentile(boots[c], 97.5)) / N
            dbk = int((filt(a).D < c).sum())
            print(f"     fix younger than {c}d at public disclosure: {100 * est:.1f}% (95% CI {100 * lo:.1f}-{100 * hi:.1f}) "
                  f"[curated {pct(k0, len(n0))}; census {pct(kA, len(nA))}; sample {100 * pB:.1f}%] "
                  f"| database clock same units: {pct(dbk, len(filt(a)))}")

    allf = lambda d: d
    stratified(allf, "ALL units")
    stratified(lambda d: d[d.ecosystem == "PyPI"], "PyPI")
    stratified(lambda d: d[d.ecosystem == "npm"], "npm")
    stratified(lambda d: d[d.sev2 == "critical/high"], "critical/high")
    stratified(lambda d: d[d.ai_stack], "LLM/agent stack")
    stratified(lambda d: d[d.P < pd.Timestamp("2026-01-01", tz="UTC")], "database entry 2022-2025")
    stratified(lambda d: d[d.P < pd.Timestamp("2026-05-01", tz="UTC")], "database entry before May 2026 (pre-backlog)")
    stratified(lambda d: d[d.P >= pd.Timestamp("2026-01-01", tz="UTC")], "database entry 2026")
    stratified(lambda d: d[d.D >= -30] if "D_pub" not in d else d[(d.D >= -30)], "excluding D < -30")
    stratified(lambda d: d.sort_values("package").groupby(["ecosystem", "cluster"]).head(1), "one unit per advisory cluster")

    # extra days under a 7-day cooldown, observed repo units
    sub7 = obs[obs.D_pub < 7]
    extra = np.where(sub7.D_pub >= 0, 7 - sub7.D_pub, 7)
    print(f"  7d cooldown, observed repo units with fix younger than 7d at disclosure: n={len(sub7)}, median extra days on a "
          f"publicly known vulnerable version = {np.median(extra):.2f}")
    # public but not (yet) in the reviewed database
    in_osv = set(g.upper() for x in a.ghsa_ids for g in x.split(";"))
    osv_all = set(pd.read_csv(os.path.join(DATA, "advisories.csv"), usecols=["ghsa_id"]).ghsa_id.str.upper())
    lst = pd.DataFrame(listed, columns=["repo", "ghsa", "published_at"])
    lst["in_osv"] = lst.ghsa.isin(osv_all)
    lst["month"] = lst.published_at.dt.strftime("%Y-%m")
    recent = lst[lst.published_at >= pd.Timestamp("2026-01-01", tz="UTC")]
    print("  repository advisories listed by fetched repos (census + sample), by month of maintainer publication, 2026: "
          "in reviewed database (with fixed version) / not")
    for m, g in recent.groupby("month"):
        print(f"    {m}: {int(g.in_osv.sum())} / {int((~g.in_osv).sum())}")


def public_clock_full(a, block):
    """Exact public-disclosure clock using every repo's published repository advisories (stage 5, full population)."""
    path = os.path.join(DATA, "raw", "repo_advisories_full.jsonl")
    if not os.path.exists(path):
        print("  full repo pull not collected"); return
    pub, rstat, listed = {}, {}, []
    for line in open(path):
        d = json.loads(line)
        rstat[d["repo"]] = d["status"]
        for x in d["advisories"]:
            if x.get("ghsa_id") and x.get("published_at"):
                pub[x["ghsa_id"].upper()] = ts(x["published_at"])
                ecos = {(v.get("package") or {}).get("ecosystem") for v in x.get("vulnerabilities", [])}
                if ecos & {"pip", "npm"}:
                    listed.append((d["repo"], x["ghsa_id"].upper(), ts(x["published_at"]),
                                   bool(any(v.get("patched_versions") for v in x.get("vulnerabilities", [])
                                            if (v.get("package") or {}).get("ecosystem") in ("pip", "npm")))))
    print(f"  repos fetched: {len(rstat)}; status {pd.Series(rstat).value_counts().to_dict()}; repository advisories listed: {len(listed)}")
    a = a.copy()

    def pub_time(r):
        t = [pub[g.upper()] for g in r.ghsa_ids.split(";") if g.upper() in pub]
        return min(t) if t else pd.NaT
    a["P_repo"] = a.apply(pub_time, axis=1)
    a["is_repo"] = a.repo != ""
    print(f"  units: {len(a)}; repository-advisory units {int(a.is_repo.sum())}; maintainer publication time found for "
          f"{int(a.P_repo.notna().sum())} ({pct(int((a.is_repo & a.P_repo.notna()).sum()), int(a.is_repo.sum()))} of repository-advisory units)")
    print(f"  units whose maintainer time is later than the database time (should be ~0): {int((a.P_repo > a.P).sum())}")
    a["P_pub"] = a[["P", "P_repo"]].min(axis=1)
    a["D_pub"] = (a.P_pub - a.F_hi).dt.total_seconds() / 86400
    a["lag"] = (a.P - a.P_repo).dt.total_seconds() / 86400
    r = a[a.P_repo.notna()]
    print("  database entry minus maintainer publication (days), units with a maintainer time:")
    for lab, g in [("2022-2025", r[r.P_pub < pd.Timestamp("2026-01-01", tz="UTC")]),
                   ("2026", r[r.P_pub >= pd.Timestamp("2026-01-01", tz="UTC")])]:
        l = g.lag
        print(f"    {lab}: n={len(l)} median={l.median():.2f} p75={l.quantile(.75):.2f} share>7d={pct(int((l > 7).sum()), len(l))}")
    hrs = r.D_pub * 24
    print(f"  maintainer-advisory units: median fix-to-publication = {hrs.median():.1f} h (PyPI {hrs[r.ecosystem == 'PyPI'].median():.1f} h, "
          f"npm {hrs[r.ecosystem == 'npm'].median():.1f} h)")
    print(f"  maintainer-advisory units: published before the fix reached the registry (D_pub<0): {pct(int((r.D_pub < 0).sum()), len(r))}; "
          f"within 24h after or before (D_pub<1): {pct(int((r.D_pub < 1).sum()), len(r))}")

    def blk(df, label):
        n = len(df)
        if n == 0:
            return
        parts = [f"  -- {label} (n={n}) --"]
        for c in COOLDOWNS:
            parts.append(f"     fix younger than {c}d at public disclosure: {pct(int((df.D_pub < c).sum()), n)}"
                         f"   | database clock: {pct(int((df.D < c).sum()), n)}")
        print("\n".join(parts))
    blk(a, "ALL units")
    blk(a[a.ecosystem == "PyPI"], "PyPI")
    blk(a[a.ecosystem == "npm"], "npm")
    blk(a[a.sev2 == "critical/high"], "critical/high")
    blk(a[a.P_pub < pd.Timestamp("2026-01-01", tz="UTC")], "public disclosure 2022-2025")
    blk(a[a.P_pub >= pd.Timestamp("2026-01-01", tz="UTC")], "public disclosure 2026")
    blk(a[a.D_pub >= -30], "excluding D_pub < -30")
    blk(a.sort_values("package").groupby(["ecosystem", "cluster"]).head(1), "one unit per advisory cluster")
    blk(r, "maintainer-advisory units only")
    blk(a[~a.is_repo], "GitHub-curated advisory units only")
    pk = a.groupby(["ecosystem", "package"]).D_pub.apply(lambda d: (d < 7).mean())
    print(f"  each package weighted equally: mean per-package share with fix younger than 7d at first publication = "
          f"{100 * pk.mean():.1f}% over {len(pk)} packages")
    oc = a[a.repo != "openclaw/openclaw"]
    print(f"  excluding openclaw/openclaw: {pct(int((oc.D_pub < 7).sum()), len(oc))}")
    sub7 = a[a.D_pub < 7]
    extra = np.where(sub7.D_pub >= 0, 7 - sub7.D_pub, 7)
    print(f"  7d cooldown: units with fix younger than 7d at public disclosure n={len(sub7)}; median days the cooldown keeps the fix "
          f"out after public disclosure = {np.median(extra):.2f}; share blocked >= 6 days: {pct(int((extra >= 6).sum()), len(sub7))}")
    a.drop(columns=["all_fix_times"]).to_csv(os.path.join(DATA, "units_public_clock.csv"), index=False)
    # public but not (yet) in the reviewed database
    osv_all = set(pd.read_csv(os.path.join(DATA, "advisories.csv"), usecols=["ghsa_id"]).ghsa_id.str.upper())
    lst = pd.DataFrame(listed, columns=["repo", "ghsa", "published_at", "has_patch"]).drop_duplicates("ghsa")
    print(f"  distinct repository advisories naming a pip or npm package: {len(lst)} (duplicates across renamed/transferred repos removed)")
    lst["in_db"] = lst.ghsa.isin(osv_all)
    lst["month"] = lst.published_at.dt.strftime("%Y-%m")
    rec = lst[lst.published_at >= pd.Timestamp("2026-01-01", tz="UTC")]
    print("  repository advisories naming a pip/npm package published by maintainers in these repos, 2026, by month (distinct GHSA): in reviewed PyPI/npm data with a fixed version / not")
    for m, g in rec.groupby("month"):
        print(f"    {m}: {int(g.in_db.sum())} / {int((~g.in_db).sum())}  (not in db but listing a patched version: {int(((~g.in_db) & g.has_patch).sum())})")
    return a


def main():
    adv = pd.read_csv(os.path.join(DATA, "advisories.csv"), dtype=str, keep_default_na=False)
    reg, status = load_registry()
    print("== Inputs ==")
    print("fix events (advisory x package x fixed version) in advisories.csv:", len(adv))
    print("GHSA advisories with >=1 fixed version, by ecosystem:", adv.groupby("ecosystem").ghsa_id.nunique().to_dict())

    # Cluster duplicate GHSA advisories (one GHSA lists the other as an alias). After review round 1, advisories that
    # merely share a CVE are no longer merged (that merged reissued/incomplete-fix advisories and produced spurious negative gaps).
    uf = UF()
    for _, r in adv.iterrows():
        e = r.ecosystem
        uf.find((e, r.ghsa_id))
        for o in filter(None, r.other_osv_ids.split(";")):
            if o.startswith("GHSA-"):
                uf.union((e, r.ghsa_id), (e, o))
    adv["cluster"] = [uf.find((e, g))[1] for e, g in zip(adv.ecosystem, adv.ghsa_id)]

    sev_rank = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "MEDIUM": 2, "LOW": 1, "": 0}
    units = []
    for (eco, cl, pkg), g in adv.groupby(["ecosystem", "cluster", "package"]):
        dates = []
        for col in ["ghsa_published", "nvd_published_at_osv", "other_osv_published_min"]:
            dates += [ts(x) for x in g[col] if x]
        dates = [d for d in dates if not pd.isna(d)]
        P = min(dates)
        P_ghsa = min(ts(x) for x in g.ghsa_published if x)
        sev = max(g.severity, key=lambda s: sev_rank.get(s, 0))
        fixed = sorted(set(g.fixed_version), key=lambda v: vkey(eco, v))
        times = reg.get((eco, pkg))
        st = status.get((eco, pkg), "missing")
        ftimes = {}
        if times is not None:
            for v in fixed:
                t = lookup_time(eco, times, v)
                if t:
                    ftimes[v] = ts(t)
        hi = fixed[-1]
        repos = sorted({x.lower() for x in g.repo_advisory_repo if x})
        units.append({
            "ecosystem": eco, "cluster": cl, "package": pkg, "ghsa_ids": ";".join(sorted(set(g.ghsa_id))),
            "severity": sev, "P": P, "P_ghsa": P_ghsa, "registry_status": st,
            "n_fixed": len(fixed), "fixed_hi": hi, "F_hi": ftimes.get(hi, pd.NaT),
            "F_earliest": min(ftimes.values()) if ftimes else pd.NaT,
            "all_fix_times": ftimes, "ai_stack": is_ai(eco, pkg),
            "repo": repos[0] if repos else "",
        })
    u = pd.DataFrame(units)
    print("\n== Units: (advisory cluster, package) pairs ==")
    print("all units:", len(u), u.groupby("ecosystem").size().to_dict())
    u_win = u[(u.P >= WINDOW_START) & (u.P < WINDOW_END)].copy()
    print("units with disclosure 2022-01-01..2026-09-30:", len(u_win), u_win.groupby("ecosystem").size().to_dict())
    print("  registry status:", u_win.registry_status.value_counts().to_dict())
    miss = u_win[u_win.F_hi.isna()]
    print("  excluded, highest fixed version not found on registry:", len(miss), miss.groupby("ecosystem").size().to_dict())
    a = u_win[u_win.F_hi.notna()].copy()
    a["D"] = (a.P - a.F_hi).dt.total_seconds() / 86400
    a["D_earliest"] = (a.P - a.F_earliest).dt.total_seconds() / 86400
    a["D_ghsa"] = (a.P_ghsa - a.F_hi).dt.total_seconds() / 86400
    a["year"] = a.P.dt.year
    a["sev2"] = np.where(a.severity.isin(["CRITICAL", "HIGH"]), "critical/high", "moderate/low")
    print("analysed units:", len(a), a.groupby("ecosystem").size().to_dict())
    print("distinct packages analysed:", a.groupby("ecosystem").package.nunique().to_dict())
    print("severity:", a.severity.value_counts().to_dict())

    def block(df, label, col="D"):
        n = len(df)
        d = df[col]
        out = [f"-- {label} (n={n}) --"]
        if n == 0:
            print("\n".join(out)); return
        out.append(f"  advisory at or before fix upload (D<=0): {pct(int((d <= 0).sum()), n)}")
        out.append(f"  advisory before fix upload (D<0): {pct(int((d < 0).sum()), n)}")
        q = np.percentile(d, [25, 50, 75])
        out.append(f"  D days: p25={q[0]:.2f} median={q[1]:.2f} p75={q[2]:.2f}")
        for c in COOLDOWNS:
            k = int((d < c).sum())
            out.append(f"  fix younger than {c}d at disclosure (D<{c}): {pct(k, n)}")
        sub = d[d < 7]
        extra = np.where(sub >= 0, 7 - sub, 7)
        if len(sub):
            out.append(f"  7d cooldown, among affected: median extra days on publicly known vulnerable version = {np.median(extra):.2f}; mean = {extra.mean():.2f}")
        print("\n".join(out))

    print("\n== Main results (fix = highest fixed version; disclosure = earliest of GHSA / NVD / aliased OSV record) ==")
    block(a, "all")
    for eco in ["PyPI", "npm"]:
        block(a[a.ecosystem == eco], eco)
    for s in ["critical/high", "moderate/low"]:
        block(a[a.sev2 == s], s)
    for eco in ["PyPI", "npm"]:
        block(a[(a.ecosystem == eco) & (a.sev2 == "critical/high")], f"{eco} critical/high")
    block(a[a.ai_stack], "LLM/agent-stack packages")
    for eco in ["PyPI", "npm"]:
        block(a[a.ai_stack & (a.ecosystem == eco)], f"LLM/agent-stack {eco}")
    print("  LLM/agent-stack packages analysed:", a[a.ai_stack].groupby("ecosystem").package.nunique().to_dict())
    print("  top LLM/agent-stack packages by units:", a[a.ai_stack].package.value_counts().head(12).to_dict())
    block(a[~a.ai_stack], "other packages")

    print("\n== By disclosure year: share with D<7 ==")
    for y, g in a.groupby("year"):
        print(f"  {y}: {pct(int((g.D < 7).sum()), len(g))}  (D<=0: {pct(int((g.D <= 0).sum()), len(g))})")

    print("\n== Sensitivity checks ==")
    block(a, "all, fix = earliest-uploaded fixed version", col="D_earliest")
    block(a, "all, disclosure = GHSA published only", col="D_ghsa")
    single = a[a.n_fixed == 1]
    block(single, "units with exactly one fixed version")
    # all fix events: each fixed version found on registry, disclosure P
    ev = []
    for _, r in a.iterrows():
        for v, t in r.all_fix_times.items():
            ev.append((r.P - t).total_seconds() / 86400)
    ev = np.array(ev)
    print(f"-- all fix events (n={len(ev)}) --")
    for c in COOLDOWNS:
        print(f"  D<{c}: {pct(int((ev < c).sum()), len(ev))}")
    # restrict to fixes released in window too (guards against ancient fixes with late backfilled advisories)
    rec = a[a.F_hi >= WINDOW_START]
    block(rec, "units whose fix was also uploaded 2022 or later")
    # GHSA-only unique advisories (one unit per cluster: first package alphabetically) to avoid monorepo multi-package inflation
    one = a.sort_values("package").groupby(["ecosystem", "cluster"]).head(1)
    block(one, "one unit per advisory cluster")
    # monorepo / multi-package advisories
    multi = a.groupby(["ecosystem", "cluster"]).size()
    print("  advisory clusters covering >1 package:", int((multi > 1).sum()), "of", len(multi))

    block(a[a.D >= -30], "excluding units with D < -30 (advisory > 30 days before fix upload)")

    print("\n== Outliers ==")
    print("  D < -365 (advisory > 1y before fix):", int((a.D < -365).sum()))
    print("  D > 1825 (advisory > 5y after fix):", int((a.D > 1825).sum()))


    # ------------------------------------------------------------------
    print("\n== Database clock by period (full population; D = database entry - fix upload) ==")
    pre = a[a.P < pd.Timestamp("2026-01-01", tz="UTC")]
    y26 = a[a.P >= pd.Timestamp("2026-01-01", tz="UTC")]
    block(pre, "database clock, disclosed 2022-2025")
    block(y26, "database clock, disclosed 2026 (Jan-Sep)")
    for eco in ["PyPI", "npm"]:
        block(pre[pre.ecosystem == eco], f"database clock, {eco}, 2022-2025")
    block(pre[pre.sev2 == "critical/high"], "database clock, critical/high, 2022-2025")
    block(pre[pre.ai_stack], "database clock, LLM/agent stack, 2022-2025")
    block(y26[y26.ai_stack], "database clock, LLM/agent stack, 2026")
    print("  monthly 2026, share D<7 and median D (database clock):")
    for m, g in y26.groupby(y26.P.dt.month):
        print(f"    2026-{m:02d}: {pct(int((g.D < 7).sum()), len(g))}, median D={g.D.median():.1f}")

    # ------------------------------------------------------------------
    print("\n== Public-disclosure clock, FULL population (maintainer repository-advisory publication) ==")
    public_clock_full(a, block)
    print("\n== Check: estimate from the stage-3 random sample of repos (before the full pull) ==")
    public_clock(a, block)

    # Export unit table for review
    out = a.drop(columns=["all_fix_times"]).sort_values(["ecosystem", "package", "cluster"])
    out.to_csv(os.path.join(DATA, "units.csv"), index=False)
    print("\nwrote data/units.csv")

    if "--sample" in sys.argv:
        s = a.sample(25, random_state=SEED)[["ecosystem", "ghsa_ids", "package", "fixed_hi", "F_hi", "P", "D"]]
        print(s.to_string())


if __name__ == "__main__":
    main()
