"""Spot-check 25 random games against ESPN's public game summary API:
referee, final scores, and team accepted penalties (count-yards).
Writes data/output/sanity_check.csv. Run after collect.py."""
import os, time, json, numpy as np, pandas as pd, requests
HERE = os.path.dirname(os.path.abspath(__file__)); RAW = os.path.join(HERE, "data", "raw")
TEAMMAP = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
g = pd.read_parquet(os.path.join(RAW, "games.parquet"))
g = g[(g.game_type == "REG") & g.season.between(2015, 2025)]
off = pd.read_parquet(os.path.join(RAW, "officials.parquet"))
ref = off[off.position == "Referee"].set_index("game_id").official_name
pen = pd.read_parquet(os.path.join(RAW, "penalties_2015_2025.parquet"))
pen["penalty_yards"] = pen.penalty_yards.abs()
s = g.sample(25, random_state=7)
rows = []
for _, r in s.iterrows():
    d = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={int(r.espn)}",
                     headers={"User-Agent": "python-requests faxivo-investigator-bot"}, timeout=30).json()
    espn_ref = next((o["displayName"] for o in d["gameInfo"].get("officials", []) if o["position"]["name"] == "Referee"), None)
    comp = d["header"]["competitions"][0]["competitors"]
    sc = {c["homeAway"]: int(c["score"]) for c in comp}
    pe = {}
    for t in d["boxscore"]["teams"]:
        v = next(x["displayValue"] for x in t["statistics"] if x["name"] == "totalPenaltiesYards")
        pe[t["homeAway"] if "homeAway" in t else t["team"]["abbreviation"]] = v
    ht, at = TEAMMAP.get(r.home_team, r.home_team), TEAMMAP.get(r.away_team, r.away_team)
    p = pen[pen.old_game_id == r.old_game_id]
    ours = {k: f"{(p.penalty_team==tm).sum()}-{int(p[p.penalty_team==tm].penalty_yards.sum())}" for k, tm in (("home", ht), ("away", at))}
    rows.append(dict(game_id=r.game_id, our_ref=ref.get(r.old_game_id), espn_ref=espn_ref,
                     our_score=f"{int(r.home_score)}-{int(r.away_score)}", espn_score=f"{sc['home']}-{sc['away']}",
                     our_pen_home=ours["home"], espn_pen_home=pe.get("home"), our_pen_away=ours["away"], espn_pen_away=pe.get("away")))
    time.sleep(1)
out = pd.DataFrame(rows); out.to_csv(os.path.join(HERE, "data", "output", "sanity_check.csv"), index=False)
print(out.to_string())
print("referee match:", (out.our_ref == out.espn_ref).sum(), "/", out.espn_ref.notna().sum(), "games where ESPN lists officials")
print("score match:", (out.our_score == out.espn_score).sum(), "/", len(out))
