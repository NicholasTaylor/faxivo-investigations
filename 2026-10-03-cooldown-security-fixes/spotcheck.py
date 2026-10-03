"""Hand-check: re-fetch a random sample of analysed units live from api.osv.dev and the registries and compare
published / fix-upload times with data/units.csv. Usage: python spotcheck.py [N]"""
import sys
import time
from urllib.parse import quote

import pandas as pd
import requests

N = int(sys.argv[1]) if len(sys.argv) > 1 else 25
UA = {"User-Agent": "faxivo-investigator-bot/1.0 (+https://github.com/NicholasTaylor/faxivo-investigations)"}
u = pd.read_csv("data/units.csv")
s = u.sample(N, random_state=7)
ok = 0
for _, r in s.iterrows():
    gid = r.ghsa_ids.split(";")[0]
    osv = requests.get(f"https://api.osv.dev/v1/vulns/{gid}", headers=UA, timeout=60).json()
    if r.ecosystem == "PyPI":
        j = requests.get(f"https://pypi.org/pypi/{quote(r.package)}/{quote(str(r.fixed_hi))}/json", headers=UA, timeout=60).json()
        up = min(f["upload_time_iso_8601"] for f in j["urls"]) if j.get("urls") else None
    else:
        name = quote(r.package, safe="@").replace("/", "%2F")
        j = requests.get(f"https://registry.npmjs.org/{name}", headers=UA, timeout=120).json()
        up = j["time"].get(str(r.fixed_hi))
    fixed = sorted({ev["fixed"] for a in osv.get("affected", []) if a["package"]["name"] == r.package
                    for rg in a.get("ranges", []) for ev in rg.get("events", []) if "fixed" in ev})
    F_live = pd.Timestamp(up)
    F_ok = abs((F_live - pd.Timestamp(r.F_hi)).total_seconds()) < 1
    gp = pd.Timestamp(osv["published"])
    P_ok = pd.Timestamp(r.P) <= gp
    fx_ok = str(r.fixed_hi) in fixed
    ok += F_ok and P_ok and fx_ok
    print(f"{r.ecosystem:4} {gid} {r.package[:30]:30} fixed={r.fixed_hi} (listed {fixed}) F={F_live} P={r.P} ghsa_pub={gp} "
          f"D={r.D:.2f} | F_match={F_ok} P<=ghsa={P_ok} fixed_listed={fx_ok}")
    time.sleep(0.5)
print(f"\n{ok}/{N} sampled units match on fix version, fix upload time and disclosure bound")
