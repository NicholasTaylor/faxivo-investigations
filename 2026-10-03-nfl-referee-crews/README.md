# Do NFL referee crews move betting outcomes? (2015–2025)

Status: **plan (pre-registered before outcome data were analysed)**. Written 2026-10-03 ~19:10 UTC. At this point only coverage checks had been run (which seasons the officials file covers, and which 2025 referees exist). No penalty, score or line outcome had been tabulated by referee.

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
