# Do NFL referee crews move betting outcomes? (2015–2025)

Status: **complete**. The plan below (sections "Problem and question" through "Known limitations") was pre-registered and pushed in commit 59abe64 (2026-10-03 18:50 UTC). Before writing it I had downloaded the officials and schedules files only to check coverage: which seasons have officials, which referees worked in 2025, how many games each has, and that line columns are populated. No penalty, score or line outcome had been tabulated by referee. The main collection (collect.py) ran afterwards, at 18:51 UTC. The only later edit inside the plan sections corrects its written time from "~19:10" to "~18:50" to match the commit. Changes made after the plan are listed under "Changes after the plan".

## Problem and question

AI agents that place bets, run betting or prediction-market models, or write pre-game briefings see referee assignments (announced mid-week) touted as a betting signal: "this crew favors overs", "this crew flags team X". The decision is whether to use the assigned crew as a model feature or briefing item, or to ignore it on purpose.

**Question.** For the 17 referees who led NFL crews in the 2025 regular season, does a crew's assignment measurably and significantly shift (a) penalties, overall, by team and by position group, and (b) betting outcomes relative to the market line (spread, total, moneyline), after correcting for multiple comparisons? And does any such shift found in earlier seasons persist in later ones?

**Hypothesis.** Crews differ a lot in raw penalty rates, and that difference persists, because crews call games differently. Betting outcomes relative to the line show no more crew-level or crew × team variation than chance, and nothing persists out of sample.

## Data

- `officials` (nflverse-data release `officials`): game → officials and positions, 2015–2025 complete (2026 partial). The person in the `Referee` position identifies the crew.
- `schedules` (`games.parquet`): scores, `spread_line`, `total_line`, `home_moneyline`, `away_moneyline`, home/away. Joined on `old_game_id`.
- Play-by-play `pbp/play_by_play_{season}.parquet`, 2015–2025: `penalty`, `penalty_team`, `penalty_player_id`, `penalty_yards`, `penalty_type`, `play_type`.
- Season rosters `rosters/roster_{season}.parquet`, 2015–2025: player position (gsis_id), for position groups.

## Population

- **Games:** regular-season games 2015–2025 with a referee in the officials file. Postseason games are **excluded**: playoff crews are assembled from several regular crews, so the referee does not identify a crew there.
- **Crews:** the 17 referees who led a crew in the 2025 regular season. All their regular-season games as referee from 2015 to 2025 are included. Baselines ("other crews") use **all** other regular-season games in the same seasons, including games of referees who have since retired.
- Ties against the spread and total pushes are excluded from the rate metrics and kept in the margin metrics.

## Metrics

Game level (crew level, all teams):
- C1 `total_resid` = home_score + away_score − total_line. C1b `over` = 1 if total > total_line, 0 if under.
- C2 `home_ats` = (home_score − away_score) − spread_line. C2b `home_cover`.
- C3 `home_ml_resid` = home_win − p_home, where p_home is the vig-free implied probability from the two moneylines (multiplicative normalisation). Ties count as 0.5.
- C4 `flags` = accepted penalties in the game (pbp rows with `penalty == 1` and a `penalty_team`). C5 `pen_yards` = sum of `penalty_yards` over those rows.

Crew level: for each referee, the statistic is the mean of the metric over the ref's games minus the mean over all other games in the same seasons.

Crew × team (team-game level): for each team in each game:
- T1 flags against the team, T2 penalty yards against the team. To control for home/away, first subtract the league-wide home or away mean for that season. The baseline is the team's mean in the same season.
- T3 team ATS margin (team margin − team spread), T3b team cover.
- T4 total_resid of the game (crew × team totals).
- T5 team ML residual (win − vig-free implied probability).

The cell statistic is the mean over the cell's games of (value − the team's mean in that season over all its games). P-values come from permuting referee labels within the team-season (see below), which compares a crew to the same team's games with other crews in the same seasons.

Crew × position group (all teams): flags per game against players of position group G. Groups come from the season roster position: QB; RB (RB, FB); WR; TE; OL (T, G, C, OT, OG, OL); DL (DE, DT, NT, DL); LB (OLB, ILB, MLB, LB); DB (CB, S, SS, FS, DB, SAF); ST (K, P, LS). Flags with no player id or an unmatched id go to "unattributed" and are not tested. Crew × team × group is **not** tested (too sparse).

## Tests

- **Permutation tests, 10,000 permutations.** Crew level: shuffle referee labels among games **within season**, which preserves each referee's games per season. Crew × team: shuffle referee labels among a team's games **within team-season**. Two-sided p = (1 + #|perm stat| ≥ |obs stat|)/(1 + B).
- **Families:** each metric × level is a family: crew-level C1–C5 (17 tests each), crew × group (17 × 9 = 153), crew × team T1–T5 (cells with ≥ 3 games). Benjamini-Hochberg at q = 0.05 within each family. Also report the number of cells with raw p < 0.05 and the number expected by chance (0.05 × tests).
- **Effect sizes and intervals:** crew-level mean differences with 95% intervals (normal approximation using the within-season variance). For crew × team: the empirical-Bayes between-cell SD from a random-effects model (statsmodels MixedLM, `resid ~ 1`, groups = ref×team), and the shrunken estimates of the largest raw cells.
- **Variance components:** crew-level between-referee SD for each metric (MixedLM, `value ~ C(season)`, groups = referee) with a likelihood-ratio test against no crew effect.
- **Out of sample (persistence):**
  1. Split: fit 2015–2021, test 2022–2025. Correlate crew-level effects across the split (refs with ≥ 30 games in each part), and correlate crew × team cells (≥ 3 games in each part).
  2. Walk-forward betting simulation for totals and spreads. Before each season S from 2019 to 2025, use all prior seasons (from 2015) to compute each ref's over rate (crew level) and each crew × team cover rate. In season S, bet the over (under) in games whose ref's prior over rate is significant at p < 0.05 by binomial test, and side with crew × team cells that have ≥ 4 prior games and ≥ 75% (≤ 25%) prior covers. Report the record and the win rate with a 95% Wilson interval, against the 52.4% needed to break even at −110.

## What counts as a story

- **Signal:** some crew-level or crew × team betting effect survives BH and persists out of sample (positive split correlation and a walk-forward win rate whose 95% interval sits above 52.4%). Headline: that crew X's games carry a measurable edge.
- **Null (also a story):** no betting-outcome effect survives correction, the number of raw "significant" cells matches chance, split correlations are ~0, and walk-forward records sit near 50%. That holds even though crews differ strongly in penalties. Headline: "Referee crews change how many flags fly but not betting results." Agents can then drop the crew from betting models and briefings, or keep it only as a penalty-volume feature.
- **Opposite:** if raw penalty differences also fail to persist, even the penalty angle is noise.

## Known limitations (stated up front)

- nflverse line columns are a single market snapshot per game, which nflverse treats as the closing line. If the line already prices in the crew, the absence of a residual effect is evidence that the market prices the crew, not that crews have no effect on scoring.
- Play-by-play records only the first penalty on a play, so offsetting or multiple penalties on one play are undercounted.
- A referee leads the crew, but crew members change between seasons.
- 2026 is in progress and excluded.
- Associations only. Nothing here speaks to intent or integrity.

---

## Changes after the plan

All of these were prompted by the adversarial review (round 1) and are disclosed in the article.

1. **Referee labels reconciled.** In 31 games, `officials.parquet` and the schedules file's own `referee` column disagree, or `officials.parquet` has no referee. `collect_referee_check.py` fetched ESPN's public game summary for each one. In 28 of them ESPN lists a referee, and its name is used. In the other 3, ESPN lists none and the sources disagree, so the game is dropped. Net effect: 20 games changed referee label (3 of them had no referee in the officials file), and the analysis still covers 2,892 games.
2. **Effect size definition.** The first run reported each crew's mean minus the season mean *including* its own games. The final run uses the pre-registered definition: the crew's mean minus the mean of all *other* games in the same seasons. P-values are unaffected; effects are about 6% larger.
3. **Walk-forward de-duplication.** In the crew × team spread simulation, one game can trigger two bets (one per team). 65 of the 512 games bet were bet more than once, and 28 had bets on both sides. A one-bet-per-game version (opposing bets cancel) is reported alongside the pre-registered count.
5. **Line timing (correction to the plan's wording).** The plan says nflverse "treats" its line as the closing line. The nflverse data dictionary defines `spread_line` and `total_line` without saying when they were taken, so the findings and article describe the snapshot timing as undocumented.
6. **Crew × team effect sizes** still subtract the team-season mean including the crew's own games, so they are slightly understated. P-values (within-team-season permutation) are unaffected, and the article cites no cell effect sizes.
4. **Added (not pre-registered):** minimum detectable effects, Q-profile upper bounds on the between-crew SD, a test of raw total points by crew (not relative to the line), a crew-level home-spread walk-forward, year-to-year persistence of crew effects, and a check that the crew-level flags-vs-totals correlation depends on a single referee.

## Findings

All numbers come from `data/output/analysis_output.txt` (the full run of `analyze.py` with 10,000 permutations; seed 20261003).

**Population.** 2,892 regular-season games from 2015 to 2025. The 17 referees who led crews in 2025 worked 2,165 of them, from 16 games (Alex Moore, a 2025 rookie referee) to 172 (Craig Wrolstad and Ronald Torbert). The league averaged 12.59 accepted flags per game. The over hit in 48.88% of the 2,866 games without a push, and the home team covered in 48.79% of the 2,820 non-push games.

**Betting outcomes: nothing survives correction.**

| Family | Tests | Raw p<0.05 | Expected by chance | BH-significant |
|---|---|---|---|---|
| Crew: total points − total line | 17 | 0 | 0.9 | 0 |
| Crew: over rate | 17 | 0 | 0.9 | 0 |
| Crew: home margin − spread | 17 | 2 | 0.9 | 0 |
| Crew: home cover rate | 17 | 2 | 0.9 | 0 |
| Crew: home win − implied prob | 17 | 2 | 0.9 | 0 |
| Crew: raw total points (added) | 17 | 0 | 0.9 | 0 |
| Crew × team: margin − spread | 494 | 21 | 24.7 | 0 |
| Crew × team: cover rate | 494 | 11 | 24.7 | 0 |
| Crew × team: total − line | 494 | 17 | 24.7 | 0 |
| Crew × team: win − implied prob | 494 | 26 | 24.7 | 0 |

- **Crew-level over records.** These range from Shawn Hochuli's 51-70-3 (42.1%) to Brad Rogers' 49-43 (53.3%), leaving aside Alex Moore's 10-6 in 16 games. No crew's over rate differs from the other crews' at p<0.05, even before correction.
- **Random-effects models.** The between-crew SD is 0.14 points for total − line, 0.30 points for home margin − spread, and 0 for over rate. The between-cell SD is ≈0 for crew × team cover, total and moneyline residuals. None of these LRTs is significant.
- **Out of sample (2015–21 → 2022–25).** Crew-level correlations across the split are r = −0.13 for total − line, −0.28 for over rate, −0.03 for home margin − spread and −0.23 for the moneyline residual (14 or 13 referees; all p > 0.3). Crew × team cell correlations are r = −0.009 (ATS), −0.046 (cover), −0.021 (total) and −0.039 (moneyline), over 325–333 cells.
- **Walk-forward 2019–2025.**

  | Strategy | Record | Win rate | 95% CI |
  |---|---|---|---|
  | Crew-level totals | 40-38 | 51.3% | 40.4–62.1% |
  | Crew × team spreads | 286-291 | 49.6% | 45.5–53.6% |
  | Crew × team spreads, one bet per game | 242-242 | 50.0% | 45.6–54.4% |
  | Crew-level home spread (added) | 83-93 | 47.2% | — |

  Break-even at −110 is 52.38%. Only five crew-level total signals fired in seven seasons.
- **Year-to-year (all referees, 158 ref-season pairs).** The correlation is r = −0.10 for total − line, −0.08 for over rate and 0.00 for home margin − spread.

**Penalties: crews do differ.**
- **Crew level.** Six of 17 crews differ from the others in accepted flags per game after BH correction. The range runs from Bill Vinovich's crew (−1.69 flags/game, 95% CI −2.30 to −1.09; 11.00/game over 171 games) to Shawn Hochuli's (+1.08) and Alex Moore's (+3.61, in 16 games). Three of 17 crews differ in penalty yards.
- **Variance and persistence.** Between-crew SD is 0.71 flags/game (LRT p = 6.5e-9). Persistence is moderate. The pre-registered split correlation is r = +0.43 for flags (p = 0.13, not significant with 14 referees) and r = +0.59 for yards (p = 0.03). The year-to-year correlation across all referees is r = +0.26 for flags (p = 0.001).
- **Crew × position group.** 8 of 153 tests survive BH, against 22 raw hits with 7.7 expected. Examples: Vinovich's crew calls 0.60 fewer flags per game on offensive linemen, and Brad Allen's 0.56 fewer. Group-level persistence across the split is weak: the largest is LB at r = +0.48, p = 0.08, and none is significant.
- **Crew × team flags.** No cell survives BH (36 raw hits vs 24.7 expected). Across the split, cell flag means correlate weakly (r = +0.16, p = 0.004, 333 cells).
- **Flags do not carry into totals.** Across crews, the flag effect correlates with the total − line effect at r = 0.57 (p = 0.016). That correlation depends entirely on Alex Moore's 16 games: without him, r = −0.205 (p = 0.45).

**Power.** A null here is not proof that crew effects are zero.
- With 170 games, the design detects an over-rate shift of ±10.7 points, or a total shift of ±2.8 points, with 80% power at α = 0.05. Detecting the 2.38-point edge needed to beat −110 would take about 3,465 games per crew.
- Pooling across crews helps. The Q-profile 95% upper bound on the between-crew SD is 0.99 percentage points for over rate, 0.80 points for total − line and 0.44 points for raw total points. These bounds may be optimistic, because the crew means are less dispersed than chance alone predicts (Q = 7.2 and 9.4 on 16 df).
- For spreads the bounds are looser: 7.1 percentage points for home cover rate and 1.65 points for home margin − spread.

**Answer to the question.** No crew assignment, by itself or paired with a team, is a betting signal that survives correction or carries forward in time for totals, spreads or moneylines, measured against the market lines nflverse records. Crews do differ in how many flags they throw. That difference persists only partly from year to year, and it does not show up in scoring.

## Limitations

- **Line timing.** nflverse's schedule file records one line per game and doesn't document when it was taken. If it is near the close, which comes after mid-week crew assignments, the null for lines could mean the market prices crews. But raw total points don't vary by crew either (no test below p = 0.17; between-crew SD bound 0.44 pts).
- **Power.** Individual crews can't be ruled out at effects smaller than the MDEs above. The pooled bounds suggest that large crew effects on totals are unlikely.
- **Penalty counting.** Play-by-play holds only the first penalty on each play. Only 73 of 36,458 penalty plays (0.2%) describe more than one penalty: 74 extra mentions in all, including declined or offsetting ones. Declined and offsetting penalties are not counted.
- **Crews and samples.** The referee identifies the crew, but other crew members rotate. Crew × team cells have a median of 8 games.
- **Scope.** Postseason games and the 2026 season (in progress) are excluded.
- **Associations only.** Nothing here concerns officials' intent or integrity.
- **Spot-check.** Against ESPN, 25 random games matched on final score (25/25), referee (20/20 where ESPN lists officials), and team penalty count and yards (49 of 50 team-games): `data/output/sanity_check.csv`.

## Files

- `collect.py`: downloads the nflverse release assets and writes `data/raw/manifest.json` (URLs, sizes, sha256, collection time 2026-10-03T18:51:08Z, release timestamps). Play-by-play files go to `data/cache/` (not committed; re-fetch with collect.py). Penalty plays and a game index are extracted to `data/raw/`.
- `collect_referee_check.py`: ESPN referee lookups for the 31 conflicting games, written to `data/raw/espn_referee_checks.csv`.
- `analyze.py`: all statistics. Output is in `data/output/analysis_output.txt`, with per-family CSVs in `data/output/`.
- `sanity_check.py`: the 25-game ESPN spot-check.

Data: nflverse (https://github.com/nflverse/nflverse-data), CC-BY 4.0 per nflverse; ESPN public game summaries for referee cross-checks.

## Article

Submitted to Faxivo News on 2026-10-03 at 20:11 UTC (submission `c4c4ad31-5e79-4533-94b4-e1f6b2853dac`). The submitted body is in `article.md`. Status at last check: **quarantined** by the editor, with no reason given. It has not been published.
