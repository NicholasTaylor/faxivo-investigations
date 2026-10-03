# faxivo-investigations

Data, scripts and methods behind data-journalism investigations by **investigator-bot**, filed to Faxivo News.

Each investigation lives in its own folder, `YYYY-MM-DD-<slug>/`, with:

- `README.md`: the question, the plan written before collecting data (and any declared changes to it), sources, method,
  findings, limitations and a link to the article
- `collect.py`: fetches the data (records endpoints, times and counts in `data/manifest.json` and `data/collect.log`)
- `analyze.py`: reads only from `data/` and prints every number the article uses
- `data/`: the collected snapshot, or a manifest to re-fetch it when it is too large or not redistributable

Third-party data keeps its original licence (e.g. GitHub Advisory Database records are CC-BY 4.0).
