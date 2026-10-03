"""Collect nflverse data for the referee-crew analysis.

Downloads release assets directly from github.com/nflverse/nflverse-data (no API).
Small files (officials, schedules) are kept in data/raw/. Play-by-play files are
large (~20 MB each) and cached in data/cache/ (git-ignored); the penalty plays
needed for the analysis are extracted into data/raw/penalties_2015_2025.parquet,
and per-game play counts into data/raw/pbp_game_index.parquet.
A manifest with URLs, sizes, sha256 and fetch time goes to data/raw/manifest.json.
"""
import hashlib, json, os, time, datetime
import requests
import pandas as pd

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw")
CACHE = os.path.join(HERE, "data", "cache")
SEASONS = range(2015, 2026)
UA = {"User-Agent": "faxivo-investigator-bot (data journalism; github.com/NicholasTaylor/faxivo-investigations)"}

os.makedirs(RAW, exist_ok=True); os.makedirs(CACHE, exist_ok=True)
manifest = {"collected_at_utc": None, "files": []}


def fetch(tag, name, dest_dir):
    url = f"{BASE}/{tag}/{name}"
    dest = os.path.join(dest_dir, name)
    if not os.path.exists(dest):
        for attempt in range(4):
            try:
                r = requests.get(url, headers=UA, timeout=120)
                r.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(r.content)
                break
            except Exception as e:
                print("retry", url, e); time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(url)
        time.sleep(1)
    h = hashlib.sha256(open(dest, "rb").read()).hexdigest()
    manifest["files"].append({"url": url, "path": os.path.relpath(dest, HERE),
                              "bytes": os.path.getsize(dest), "sha256": h})
    print(url, os.path.getsize(dest))
    return dest


manifest["collected_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
fetch("officials", "officials.parquet", RAW)
fetch("schedules", "games.parquet", RAW)
for tag in ("officials", "schedules", "pbp", "rosters"):
    try:
        r = requests.get(f"{BASE}/{tag}/timestamp.txt", headers=UA, timeout=30)
        manifest.setdefault("release_timestamps", {})[tag] = r.text.strip()
    except Exception:
        pass

PCOLS = ["game_id", "old_game_id", "season", "season_type", "week", "play_id", "play_type",
         "posteam", "defteam", "home_team", "away_team", "penalty", "penalty_team",
         "penalty_player_id", "penalty_player_name", "penalty_yards", "penalty_type", "desc"]
pens, idx, rosters = [], [], []
for s in SEASONS:
    p = fetch("pbp", f"play_by_play_{s}.parquet", CACHE)
    df = pd.read_parquet(p, columns=PCOLS)
    df = df[df.season_type == "REG"]
    idx.append(df.groupby(["season", "game_id", "old_game_id", "home_team", "away_team"]).size()
                 .rename("n_plays").reset_index())
    pens.append(df[(df.penalty == 1) & df.penalty_team.notna()])
    rp = fetch("rosters", f"roster_{s}.parquet", RAW)

pd.concat(pens).to_parquet(os.path.join(RAW, "penalties_2015_2025.parquet"), index=False)
pd.concat(idx).to_parquet(os.path.join(RAW, "pbp_game_index.parquet"), index=False)
manifest["notes"] = ("pbp files are cached under data/cache (not committed); penalties_2015_2025.parquet holds "
                     "regular-season plays with penalty==1 and non-null penalty_team, columns " + ",".join(PCOLS))
json.dump(manifest, open(os.path.join(RAW, "manifest.json"), "w"), indent=1)
print("done", manifest["collected_at_utc"])
