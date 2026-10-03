"""Collect data for the cooldown-vs-security-fix investigation.

Stage 1 (bulk): OSV.dev ecosystem exports for PyPI and npm. NVD publication times come from the
GHSA records themselves (database_specific.nvd_published_at).
Stage 2 (registry): upload/publish times of every version of every package that has a GitHub-reviewed
advisory with a fixed version, from the PyPI JSON API and the npm registry. Checkpointed; safe to rerun.

Outputs (data/):
  raw/osv-PyPI.zip, raw/osv-npm.zip   (large; git-ignored, see manifest)
  advisories.csv      one row per (advisory, package, fixed version) fix event, plus published dates
  raw/registry/<eco>.jsonl   one line per package: {"package", "status", "times": {version: iso}}
  manifest.json       what was fetched, when, from where, and sizes / sha256
"""
import csv
import hashlib
import io
import json
import random
import re
import os
import sys
import time
import zipfile
from datetime import datetime, timezone
from urllib.parse import quote

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw")
REG = os.path.join(RAW, "registry")
LOG = os.path.join(DATA, "collect.log")
UA = "faxivo-investigator-bot/1.0 (+https://github.com/NicholasTaylor/faxivo-investigations)"
ECOS = ["PyPI", "npm"]
REPO_ADV = re.compile(r"https://github\.com/([^/]+)/([^/]+)/security/advisories/(GHSA-[a-z0-9-]+)", re.I)
REPO_SAMPLE_SEED = 20261003
REPO_SAMPLE_SIZE = 140

S = requests.Session()
S.headers["User-Agent"] = UA


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, path):
    if os.path.exists(path):
        return
    log(f"GET {url}")
    with S.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(path + ".part", "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.replace(path + ".part", path)


def stage1(manifest):
    os.makedirs(RAW, exist_ok=True)
    files = {}
    for eco in ECOS:
        url = f"https://osv-vulnerabilities.storage.googleapis.com/{eco}/all.zip"
        p = os.path.join(RAW, f"osv-{eco}.zip")
        download(url, p)
        files[p] = url
    manifest["bulk_files"] = [
        {"url": u, "file": os.path.relpath(p, HERE), "bytes": os.path.getsize(p), "sha256": sha256(p),
         "mtime_utc": datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).isoformat()}
        for p, u in files.items()
    ]

    # Parse OSV
    rows = []
    cve_ids = set()
    counts = {}
    for eco in ECOS:
        z = zipfile.ZipFile(os.path.join(RAW, f"osv-{eco}.zip"))
        recs = [json.loads(z.read(n)) for n in z.namelist() if n.endswith(".json")]
        c = {"records": len(recs)}
        # published time per id, for alias lookup within the ecosystem export
        pub_by_id = {r["id"]: r.get("published") for r in recs}
        alias_pub = {}
        for r in recs:
            for a in r.get("aliases", []) + [r["id"]]:
                alias_pub.setdefault(a, []).append((r["id"], r.get("published")))
        ghsa = [r for r in recs if r["id"].startswith("GHSA-")]
        c["ghsa"] = len(ghsa)
        c["ghsa_withdrawn"] = sum(1 for r in ghsa if r.get("withdrawn"))
        for r in ghsa:
            if r.get("withdrawn"):
                continue
            aliases = r.get("aliases", [])
            cves = [a for a in aliases if a.startswith("CVE-")]
            cve_ids.update(cves)
            # other OSV records in this export that share an alias with this advisory (e.g. PYSEC)
            other = set()
            for a in aliases + [r["id"]]:
                for rid, p in alias_pub.get(a, []):
                    if rid != r["id"] and not rid.startswith("MAL-"):
                        other.add((rid, p))
            other_pub = min((p for _, p in other if p), default="")
            ds = r.get("database_specific", {})
            repo_adv = ""
            for ref in r.get("references", []):
                m = REPO_ADV.match(ref.get("url", ""))
                if m and m.group(3).upper() == r["id"].upper():
                    repo_adv = f"{m.group(1)}/{m.group(2)}"
                    break
            for aff in r.get("affected", []):
                pkg = aff.get("package", {})
                if pkg.get("ecosystem") != eco:
                    continue
                fixed = []
                for rg in aff.get("ranges", []):
                    if rg.get("type") not in ("ECOSYSTEM", "SEMVER"):
                        continue
                    for ev in rg.get("events", []):
                        if "fixed" in ev:
                            fixed.append(ev["fixed"])
                for fv in sorted(set(fixed)):
                    rows.append({
                        "ecosystem": eco, "ghsa_id": r["id"], "package": pkg["name"], "fixed_version": fv,
                        "n_fixed_versions": len(set(fixed)),
                        "ghsa_published": r.get("published", ""), "ghsa_modified": r.get("modified", ""),
                        "github_reviewed_at": ds.get("github_reviewed_at") or "",
                        "nvd_published_at_osv": ds.get("nvd_published_at") or "",
                        "severity": ds.get("severity") or "",
                        "repo_advisory_repo": repo_adv,
                        "cves": ";".join(cves),
                        "other_osv_ids": ";".join(sorted(i for i, _ in other)),
                        "other_osv_published_min": other_pub,
                    })
        counts[eco] = c
    with open(os.path.join(DATA, "advisories.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    log(f"advisories.csv: {len(rows)} fix events; counts {counts}")
    manifest["osv_counts"] = counts
    manifest["fix_events"] = len(rows)

    manifest["cve_aliases"] = len(cve_ids)


def fetch_pypi(name):
    r = S.get(f"https://pypi.org/pypi/{quote(name)}/json", timeout=120)
    if r.status_code == 404:
        return "not_found", {}
    r.raise_for_status()
    d = r.json()
    times = {}
    for ver, files in d.get("releases", {}).items():
        ts = [f["upload_time_iso_8601"] for f in files if f.get("upload_time_iso_8601")]
        if ts:
            times[ver] = min(ts)
    return "ok", times


def fetch_npm(name):
    enc = quote(name, safe="@") .replace("/", "%2F")
    r = S.get(f"https://registry.npmjs.org/{enc}", timeout=300)
    if r.status_code == 404:
        return "not_found", {}
    r.raise_for_status()
    d = r.json()
    t = d.get("time", {})
    times = {k: v for k, v in t.items() if k not in ("created", "modified")}
    return "ok", times


def stage2(manifest):
    os.makedirs(REG, exist_ok=True)
    pkgs = {e: set() for e in ECOS}
    with open(os.path.join(DATA, "advisories.csv")) as f:
        for row in csv.DictReader(f):
            pkgs[row["ecosystem"]].add(row["package"])
    for eco in ECOS:
        out = os.path.join(REG, f"{eco}.jsonl")
        done = set()
        if os.path.exists(out):
            with open(out) as f:
                for line in f:
                    try:
                        done.add(json.loads(line)["package"])
                    except Exception:
                        pass
        todo = sorted(pkgs[eco] - done)
        log(f"{eco}: {len(pkgs[eco])} packages, {len(done)} done, {len(todo)} to fetch")
        fn = fetch_pypi if eco == "PyPI" else fetch_npm
        with open(out, "a") as f:
            for i, name in enumerate(todo):
                for attempt in range(5):
                    try:
                        status, times = fn(name)
                        break
                    except Exception as e:  # network / 5xx / 429
                        wait = 10 * (attempt + 1)
                        log(f"{eco} {name}: {e!r}; retry in {wait}s")
                        time.sleep(wait)
                else:
                    status, times = "error", {}
                f.write(json.dumps({"package": name, "status": status,
                                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                    "times": times}) + "\n")
                f.flush()
                if i % 200 == 0:
                    log(f"{eco}: {i}/{len(todo)}")
                time.sleep(0.25)
        manifest.setdefault("registry", {})[eco] = {"packages": len(pkgs[eco])}
    log("stage2 done")


def repo_population():
    """Repos with GitHub repository advisories (advisory references its own repo advisory URL), advisories
    published by GitHub in 2022 or later. Lowercased owner/repo, sorted."""
    pop = set()
    with open(os.path.join(DATA, "advisories.csv")) as f:
        for row in csv.DictReader(f):
            if row["repo_advisory_repo"] and row["ghsa_published"] >= "2022-01-01":
                pop.add(row["repo_advisory_repo"].lower())
    return sorted(pop)


def stage3(manifest):
    """Simple random sample of repos; for each, list its published repository security advisories (GitHub REST,
    unauthenticated, 60 requests/hour) to get the maintainer's publication time (published_at)."""
    pop = repo_population()
    rng = random.Random(REPO_SAMPLE_SEED)
    sample = rng.sample(pop, REPO_SAMPLE_SIZE)
    with open(os.path.join(DATA, "repo_sample.txt"), "w") as f:
        f.write("\n".join(sample) + "\n")
    out = os.path.join(RAW, "repo_advisories.jsonl")
    done = set()
    if os.path.exists(out):
        with open(out) as f:
            for line in f:
                done.add(json.loads(line)["repo"])
    log(f"stage3: population {len(pop)} repos, sample {len(sample)}, done {len(done)}")
    manifest["repo_population"] = len(pop)
    manifest["repo_sample_size"] = len(sample)
    manifest["repo_sample_seed"] = REPO_SAMPLE_SEED
    for i, repo in enumerate(sample):
        if repo in done:
            continue
        advs, status, page = [], "ok", 1
        while True:
            url = f"https://api.github.com/repos/{repo}/security-advisories?state=published&per_page=100&page={page}"
            r = S.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=60)
            rem = int(r.headers.get("X-RateLimit-Remaining", "1"))
            reset = int(r.headers.get("X-RateLimit-Reset", str(int(time.time()) + 60)))
            if r.status_code in (403, 429) and rem == 0:
                wait = max(reset - time.time(), 0) + 5
                log(f"rate limited; sleeping {wait:.0f}s")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                status = f"http_{r.status_code}"
                break
            batch = r.json()
            advs += [{"ghsa_id": a.get("ghsa_id"), "published_at": a.get("published_at"),
                      "created_at": a.get("created_at"), "updated_at": a.get("updated_at"),
                      "cve_id": a.get("cve_id"), "severity": a.get("severity")} for a in batch]
            if rem <= 1:
                wait = max(reset - time.time(), 0) + 5
                log(f"rate limit reached; sleeping {wait:.0f}s")
                time.sleep(wait)
            if len(batch) < 100:
                break
            page += 1
        with open(out, "a") as f:
            f.write(json.dumps({"repo": repo, "status": status, "fetched_at":
                                datetime.now(timezone.utc).isoformat(timespec="seconds"), "advisories": advs}) + "\n")
        log(f"stage3 {i + 1}/{len(sample)} {repo}: {status}, {len(advs)} advisories")
    log("stage3 done")


CENSUS_SIZE = 25


def census_list():
    """The CENSUS_SIZE repos with the most (advisory, package) fix units (GHSA published >= 2022). Added after the first
    adversarial review: the random sample contained none of the largest repos, which hold a large share of units."""
    cnt = {}
    seen = set()
    with open(os.path.join(DATA, "advisories.csv")) as f:
        for row in csv.DictReader(f):
            repo = row["repo_advisory_repo"].lower()
            key = (row["ghsa_id"], row["package"])
            if repo and row["ghsa_published"] >= "2022-01-01" and key not in seen:
                seen.add(key)
                cnt[repo] = cnt.get(repo, 0) + 1
    return sorted(cnt, key=lambda r: (-cnt[r], r))[:CENSUS_SIZE], cnt


def fetch_repo_advisories(repo):
    advs, status, page = [], "ok", 1
    while True:
        url = f"https://api.github.com/repos/{repo}/security-advisories?state=published&per_page=100&page={page}"
        r = S.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=60)
        rem = int(r.headers.get("X-RateLimit-Remaining", "1"))
        reset = int(r.headers.get("X-RateLimit-Reset", str(int(time.time()) + 60)))
        if r.status_code in (403, 429) and rem == 0:
            wait = max(reset - time.time(), 0) + 5
            log(f"rate limited; sleeping {wait:.0f}s")
            time.sleep(wait)
            continue
        if r.status_code != 200:
            status = f"http_{r.status_code}"
            break
        batch = r.json()
        advs += [{"ghsa_id": a.get("ghsa_id"), "published_at": a.get("published_at"),
                  "created_at": a.get("created_at"), "updated_at": a.get("updated_at"),
                  "cve_id": a.get("cve_id"), "severity": a.get("severity"),
                  "vulnerabilities": [{"package": (v.get("package") or {}), "patched_versions": v.get("patched_versions")}
                                      for v in (a.get("vulnerabilities") or [])]} for a in batch]
        if rem <= 1:
            wait = max(reset - time.time(), 0) + 5
            log(f"rate limit reached; sleeping {wait:.0f}s")
            time.sleep(wait)
        if len(batch) < 100:
            break
        page += 1
    return advs, status


def stage4(manifest):
    census, cnt = census_list()
    with open(os.path.join(DATA, "repo_census.txt"), "w") as f:
        f.write("\n".join(census) + "\n")
    out = os.path.join(RAW, "repo_advisories.jsonl")
    done = set()
    if os.path.exists(out):
        with open(out) as f:
            for line in f:
                done.add(json.loads(line)["repo"])
    log(f"stage4: census of top {len(census)} repos by units ({sum(cnt[r] for r in census)} units); already fetched {len(set(census) & done)}")
    manifest["repo_census_size"] = len(census)
    for i, repo in enumerate(census):
        if repo in done:
            continue
        advs, status = fetch_repo_advisories(repo)
        with open(out, "a") as f:
            f.write(json.dumps({"repo": repo, "status": status, "stratum": "census", "fetched_at":
                                datetime.now(timezone.utc).isoformat(timespec="seconds"), "advisories": advs}) + "\n")
        log(f"stage4 {i + 1}/{len(census)} {repo}: {status}, {len(advs)} advisories")
    log("stage4 done")


def stage5(manifest):
    """Full population: every repo with a repository advisory (GHSA published >= 2022), authenticated GitHub REST
    (token read from the file in $GITHUB_TOKEN_FILE, default ~/.config/faxivo/github_token; never logged or saved).
    Added once a token became available; supersedes the stage-3 sample and stage-4 census, which are kept as checks."""
    tf = os.environ.get("GITHUB_TOKEN_FILE", os.path.expanduser("~/.config/faxivo/github_token"))
    token = open(tf).read().strip()
    gh = requests.Session()
    gh.headers.update({"User-Agent": UA, "Accept": "application/vnd.github+json",
                       "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"})
    pop = repo_population()
    out = os.path.join(RAW, "repo_advisories_full.jsonl")
    done = set()
    if os.path.exists(out):
        with open(out) as f:
            for line in f:
                done.add(json.loads(line)["repo"])
    log(f"stage5: full population {len(pop)} repos, done {len(done)}")
    manifest["repo_full_population"] = len(pop)
    for i, repo in enumerate(pop):
        if repo in done:
            continue
        # The endpoint uses cursor pagination: follow the Link rel="next" URL (a ?page=N parameter is ignored and would
        # return the first page forever). Capped at 50 pages; ids are de-duplicated.
        advs, status, seen, pages = [], "ok", set(), 0
        url = f"https://api.github.com/repos/{repo}/security-advisories?state=published&per_page=100"
        while url and pages < 50:
            try:
                r = gh.get(url, timeout=60)
            except Exception as e:
                log(f"stage5 {repo}: {e!r}; retry in 15s")
                time.sleep(15)
                continue
            rem = int(r.headers.get("X-RateLimit-Remaining", "100"))
            reset = int(r.headers.get("X-RateLimit-Reset", str(int(time.time()) + 60)))
            if r.status_code in (403, 429) and (rem == 0 or "rate limit" in r.text.lower()):
                wait = max(reset - time.time(), 0) + 5
                log(f"stage5 rate limited; sleeping {wait:.0f}s")
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                time.sleep(10)
                continue
            if r.status_code != 200:
                status = f"http_{r.status_code}"
                break
            pages += 1
            for a in r.json():
                if a.get("ghsa_id") in seen:
                    continue
                seen.add(a.get("ghsa_id"))
                advs.append({"ghsa_id": a.get("ghsa_id"), "published_at": a.get("published_at"),
                             "created_at": a.get("created_at"), "updated_at": a.get("updated_at"),
                             "cve_id": a.get("cve_id"), "severity": a.get("severity"),
                             "vulnerabilities": [{"package": (v.get("package") or {}), "patched_versions": v.get("patched_versions")}
                                                 for v in (a.get("vulnerabilities") or [])]})
            url = r.links.get("next", {}).get("url")
            if rem < 50:
                wait = max(reset - time.time(), 0) + 5
                log(f"stage5 near rate limit; sleeping {wait:.0f}s")
                time.sleep(wait)
            if url:
                time.sleep(0.3)
        if pages >= 50 and url:
            status = "truncated_50_pages"
        with open(out, "a") as f:
            f.write(json.dumps({"repo": repo, "status": status, "fetched_at":
                                datetime.now(timezone.utc).isoformat(timespec="seconds"), "advisories": advs}) + "\n")
        if i % 100 == 0:
            log(f"stage5 {i}/{len(pop)} {repo}: {status}, {len(advs)} advisories")
        time.sleep(0.3)
    log("stage5 done")


if __name__ == "__main__":
    mp = os.path.join(DATA, "manifest.json")
    manifest = json.load(open(mp)) if os.path.exists(mp) else {}
    stages = sys.argv[1:] or ["1", "2", "5"]
    if "1" in stages:
        manifest["stage1_started_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        stage1(manifest)
        manifest["stage1_finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if "2" in stages:
        manifest.setdefault("stage2_started_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        stage2(manifest)
        manifest["stage2_finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if "3" in stages:
        manifest.setdefault("stage3_started_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        stage3(manifest)
        manifest["stage3_finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if "4" in stages:
        manifest.setdefault("stage4_started_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        stage4(manifest)
        manifest["stage4_finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if "5" in stages:
        manifest.setdefault("stage5_started_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        stage5(manifest)
        manifest["stage5_finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest["endpoints"] = {
        "osv": "https://osv-vulnerabilities.storage.googleapis.com/{PyPI,npm}/all.zip",
        "pypi": "https://pypi.org/pypi/{package}/json  (releases[version][*].upload_time_iso_8601, earliest file)",
        "npm": "https://registry.npmjs.org/{package}  (time[version])",
        "github_repo_advisories": "https://api.github.com/repos/{owner}/{repo}/security-advisories?state=published&per_page=100 (published_at)",
    }
    json.dump(manifest, open(mp, "w"), indent=2)
