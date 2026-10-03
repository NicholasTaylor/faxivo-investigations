"""Second collection step (added after adversarial review, 2026-10-03).
officials.parquet and the schedules file's own `referee` column disagree on some games.
For every regular-season game 2015-2025 where they disagree (after normalising known
name variants) or where officials.parquet has no Referee, fetch ESPN's public game
summary and record ESPN's listed referee. Output: data/raw/espn_referee_checks.csv"""
import os, time, datetime, pandas as pd, requests
HERE = os.path.dirname(os.path.abspath(__file__)); RAW = os.path.join(HERE, "data", "raw")
NORM = {"Ron Torbert": "Ronald Torbert", "John Parry": "John Parry", "Jon Parry": "John Parry", "John Perry": "John Parry"}
def norm(x): return NORM.get(x, x) if isinstance(x, str) else x
g = pd.read_parquet(os.path.join(RAW, "games.parquet"))
g = g[(g.game_type == "REG") & g.season.between(2015, 2025)]
off = pd.read_parquet(os.path.join(RAW, "officials.parquet"))
ref = off[off.position == "Referee"].drop_duplicates("game_id").set_index("game_id").official_name
g = g.assign(off_ref=g.old_game_id.map(ref).map(norm), sched_ref=g.referee.map(norm))
chk = g[(g.off_ref != g.sched_ref) | g.off_ref.isna()]
print("games to check:", len(chk))
rows = []
for _, r in chk.iterrows():
    url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={int(r.espn)}"
    try:
        d = requests.get(url, headers={"User-Agent": "python-requests faxivo-investigator-bot"}, timeout=30).json()
        e = next((o["displayName"] for o in d.get("gameInfo", {}).get("officials", []) if o["position"]["name"] == "Referee"), None)
    except Exception as ex:
        e = None; print("fail", r.game_id, ex)
    rows.append(dict(game_id=r.game_id, old_game_id=r.old_game_id, espn_event=int(r.espn), officials_ref=r.off_ref,
                     schedules_ref=r.sched_ref, espn_ref=norm(e), url=url,
                     fetched_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
    time.sleep(1)
out = pd.DataFrame(rows); out.to_csv(os.path.join(RAW, "espn_referee_checks.csv"), index=False)
print(out.to_string())
