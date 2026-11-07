# Waterloo Core v2 — evidence

What changed in routing, timing, search and the comparison view, and what real
Waterloo data showed while it was built. Every figure below came from a run on
the real network: dataset `51585450…` (180,554 segments, OpenStreetMap as of
2026-08-16, 1 m HRDEM elevation) and its place index, read from the same
extract (`cee90e37…`). Runs were made on a development laptop under heavy load
from unrelated work. Timings outside §10 are upper bounds, not performance
claims; §10's say what they measured and under what conditions.

An acceptance pass after review (§10) fixed four routing faults and a layout
fault, measured policy 3's performance, and reran every real-data record here
on commit `43574b8`. Figures in §5 and §6 are from that run.

Machine-readable records:

- [`waterloo-truth-audit.json`](waterloo-truth-audit.json): every case,
  profile, check and flag of the product truth audit.
- [`waterloo-search-quality.json`](waterloo-search-quality.json): every query
  of the search-quality corpus and what came back.
- [`waterloo-routes.json`](waterloo-routes.json): the twenty-journey corpus,
  regenerated under policy 3 (§6).
- [`waterloo-performance-policy3.json`](waterloo-performance-policy3.json):
  route and comparison latency, Dijkstra against A\*, and process memory (§10).
- [`waterloo-graph-load-policy3.json`](waterloo-graph-load-policy3.json) and
  [`waterloo-graph-load-core-v2-ab.json`](waterloo-graph-load-core-v2-ab.json):
  graph load, and graph load against `main` on the same machine (§10).
- [`FIELD_VERIFICATION_CHECKLIST.md`](FIELD_VERIFICATION_CHECKLIST.md): eight
  journeys to check on the ground. None has been.

## 1. The shortest route is a fixed baseline

The comparison is "shortest pedestrian route" against "your profile's route".
The shortest route is computed for standard walking whichever profile is
chosen. Its geometry never depended on the profile. **Its time did**: it was
re-timed at the chosen profile's pace, so switching from wheelchair to walker
changed its time although it had not moved. On the twenty-journey corpus, the
campus walk's shortest route showed 393 s for the wheelchair profile and 532 s
for the walker, on the same path.

It is now timed at walking pace, always. The truth audit checks that every field
of the shortest route is identical across all six profiles. A hash covers its
segments, line, distance and time range. The result is in §5.

## 2. Routing policy 3: one crossing is one crossing

OpenStreetMap draws a road crossing as one way from kerb to kerb, and a
stairway as one way. The network keeps every vertex, so a way arrives as one
segment per node pair. On this dataset 5,430 of 5,682 crossing ways arrive in
pieces (11,764 segments), and so do 41 of 374 stairways (437 segments). Three
things followed:

| Defect                              | What it did                                                                                                                                | How many                                                 |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------- |
| Flat penalties charged per piece    | A crossing in three pieces cost three crossing penalties. A stairway's per-step charge was applied to its whole step count once per piece. | 5,430 crossing ways, 41 stairways (17 with a step count) |
| No kerb in the middle of a crossing | A kerb is recorded on the node at each end, so the piece across the road read as "no kerb recorded".                                       | 424 crossing ways whose pieces disagree                  |
| Counts of pieces                    | "4 stairways", "13 crossings" counted segments.                                                                                            | every route                                              |

**The scenario that shows it.** Uptown Waterloo to the transit hub
(`uptown-to-transit-hub`), wheelchair. Under policy 2 the route detoured 97 m
(182.3 m → 278.9 m, +53%) through three other crossings. It avoided a
signalised crossing, OSM way 864845552, recorded as dropped at **both** ends.
That crossing is drawn in four pieces, and the two middle pieces read as "no
kerb recorded". The wheelchair profile charges 90 effective metres for an
unrecorded kerb, so the crossing paid two phantom kerb penalties and four
crossing penalties, about 220 effective metres for a crossing whose kerbs are
both recorded as dropped. Under policy 3 it is one crossing, kerb lowered, and
the route goes straight across: 183.0 m.

**The change.** Each crossing or stairway way is one _unit_. Its flat
penalties are spread over its pieces by length, so walking the whole unit pays
each once. Costs stay a sum over segments, every segment still costs at least
its length, and A* stays admissible; a unit test checks A* against Dijkstra on
split crossings. A crossing's kerb is read from every kerb recorded along it:
two or more recorded kerbs give the worst of them, one recorded kerb leaves the
other end unknown, none is unknown. The one-kerb rule is stricter than before.
Policy 2 took a single dropped kerb as the crossing's kerb when the crossing
happened to be one segment, and charged the other piece as unknown when it was
two: the same evidence, read two ways. The asymmetric-error rule decides which
reading is right. A kerb nobody recorded is not a dropped one.

**What moved, on the twenty-journey corpus** (wheelchair, walker, crutches,
stroller, reduced mobility; 95 profile routes that exist under both policies):

|                                     | Policy 2 | Policy 3 |
| ----------------------------------- | -------- | -------- |
| Profile routes that changed path    | —        | 49 of 95 |
| `uptown-to-transit-hub`, wheelchair | +96.6 m  | +0.7 m   |
| `park-to-campus`, wheelchair        | +102.9 m | +2.5 m   |
| `boardwalk-to-uptown`, wheelchair   | +227.7 m | +0.0 m   |
| `campus-to-laurier`, wheelchair     | +151.9 m | +51.5 m  |
| `victoria-park-to-market`, crutches | +26.8 m  | +41.1 m  |

Most changes remove a detour that was paying for duplicated or phantom
penalties. Some routes get longer, for two reasons. First, the stricter
one-kerb rule makes some crossings dearer. Second, a crossing that is now
cheaper can make a longer path the cheapest overall.

The searched journey in [§15 of the claims ledger](../CLAIMS_LEDGER.md) shows
the second reason. Davis Centre Library to the Student Life Centre, wheelchair,
went from 490.2 m to 503.4 m. The new route crosses a road at OSM way
893860373, an 8 m unmarked crossing recorded as dropped at both ends and drawn
in two pieces, which policy 2 charged twice. Charged once, the path through it
is the cheaper one for this profile, though 13 m longer.

**Stairway counts.** The Davis Centre journeys' shortest routes have **three**
stairways, not four. One stairway is drawn as two segments (positions 32 and 33
of the searched journey's shortest route). Every surface that quoted "4
stairways" now says 3. Policy 3 did not move the routes themselves: 287.4 m
and 406.4 m were unchanged. The acceptance pass did move the searched one (§10):
its Student Life Centre end now snaps to a segment 1.2 m from the point instead
of 1.4 m, so it is 401.3 m against 503.7 m. The verified example is still
287.4 m against 354.1 m.

`ROUTING_POLICY_VERSION` is 3.

## 3. Snapping to paths that connect

The network has 157 parts that no walkable path joins. The largest holds
152,935 of 155,714 nodes, and the second, 1,887 nodes, is RIM Park
([KI-14](../development/KNOWN_ISSUES.md)). Three failures traced to this:

- **Columbia Icefield to Federation Hall had no route for anybody.** The
  Icefield point snapped onto an indoor corridor joined to the campus only
  through `access=private` segments. The connectivity check counted those as
  connections, so the corridor looked connected while no walkable path joined
  it.
- **Physical Activities Complex to the Dana Porter Library had no wheelchair,
  walker or stroller route.** The library's nearest path is reachable only by a
  stairway in the map data.
- **Conestoga Mall to RIM Park** searched everything reachable before
  reporting "no route satisfies the standard walking profile", which was slow
  and named the wrong cause.

Parts are now numbered when the graph loads, counting only walkable segments,
and separately only step-free ones. When the nearest paths to the two ends are
in different parts, one end moves to the nearest path within the same 150 m
that connects, preferring the larger part. Otherwise the request fails at once,
saying the two points are on parts of the map that do not connect, or, for a
step-free profile, that no step-free path joins them.

A moved end must still be nearer the point it stands for than the other end is.
The first full audit found why: asked to climb the stairway by Columbia
Icefield (`stairs-1536295321`), the step-free search moved the top of the
stairs to the nearest step-free path, which was the bottom. The answer was a
route of 0 m, "29 m shorter" than the stairs. There is no step-free route to
that point, and that is now the answer.

When an end has moved, the comparison leads with it: "Ends 52 m away: the map
has no path for this profile to where the shortest route ends." Two routes that
end in different places are not one journey made two ways, so the cards say
"Ends 52 m away" instead of setting their lengths against each other, and a
caution says the same in a sentence. Since the acceptance pass the response
carries no length difference for them at all (§10). The figure was 25 m until
snapping ranked paths by metres; see §5.

## 4. Time is a range at an assumed pace

Each route's time is distance over an assumed pace **range**, labelled with
whose pace it is:

|                               | Assumed pace (m/s) |
| ----------------------------- | ------------------ |
| Shortest route (walking pace) | 1.1–1.4            |
| Wheelchair                    | 0.8–1.2            |
| Walker or rollator            | 0.5–0.8            |
| Crutches or cane              | 0.6–0.9            |
| Stroller or pram              | 1.0–1.3            |
| Reduced mobility              | 0.7–1.1            |

These are planning assumptions and nobody's measured pace. Each range contains
the single figure each profile used before. The fixed 20 s per crossing and
2.5 s per step are gone. A signal can take a minute or nothing, and nobody
walking climbs a step in 2.5 s, so the fixed figures made a guess look like a
calculation. A route card reads "5–6 min · walking pace" or "7–10 min ·
assumed wheelchair pace", with "About 5–6 min at walking pace" as its tooltip
and accessible description, and the two are never subtracted. Distance leads;
time is the second line.

## 5. The product truth audit

`pathable truth-audit --region waterloo` routes a fixed corpus of 41 journeys
under the five selectable profiles and one custom profile, wheelchair with a
declared 5% uphill limit. The corpus is
[`routing/truth_corpus.py`](../../services/api/src/pathable_api/routing/truth_corpus.py),
version 1. The journeys are campus walks, longer journeys, the longest
stairways and steep footpaths, crossings with 0, 2 and 4 kerb records, foot=no
ways, and hard snapping. They were chosen by what they are, never by what they
produced.

Each comparison is built exactly as the API builds it and then checked:

- **Defects**, which must never happen: the shortest route differing between
  profiles; a declared hard limit broken; a polyline with gaps or ending away
  from its snap point; a distance that is not the sum of its segments; a
  summary that says "same path" about two paths, or a length about two routes
  whose ends differ; a length difference reported for two routes whose ends
  differ, or missing for two whose ends match; an explanation pointing at a
  segment the chosen route uses; a time that is not distance over the stated
  pace; a response that disagrees with the objects behind it; "inf" or "nan" in
  a sentence.
- **Flags**, which a person inspects: a long detour around missing data rather
  than a recorded barrier, a profile route shorter than the shortest route, a
  detour over 300 m and 50%, a snap over 50 m from the point asked for, ends
  that moved, and a different route with no explanation. The length flags are
  raised only for routes that start and end at the same points.

Run on commit `43574b8` after the acceptance pass, routing policy 3, truth audit
and corpus version 1, on a clean tree:

|                                                 |                                                 |
| ----------------------------------------------- | ----------------------------------------------- |
| Journeys                                        | 41                                              |
| Profile comparisons                             | 246                                             |
| Compared, both routes found                     | 224: 60 on the same path, 164 on different ones |
| No route for that profile                       | 10                                              |
| No route for anybody                            | 12                                              |
| Shortest route identical under all six profiles | all 41 journeys, including the 2 with none      |
| **Defects**                                     | **0**                                           |
| Flags                                           | 15, each inspected below                        |

The first run, on `d888ba0` before the acceptance pass, also found no defect.
It had 220 compared, 14 with no route for the profile, and 18 flags. Three of
those flags, "profile route shorter" on routes that end elsewhere, are no
longer raised, because those lengths are no longer compared.

**Flags.**

- **Ends moved, 14.** On four journeys the destination moved for the three
  step-free profiles (wheelchair, walker, stroller), and on the two stairways
  for the custom profile too:
  - Physical Activities Complex to the Dana Porter Library: 52.2 m.
  - Columbia Icefield to Federation Hall: 43.4 m.
  - The tops of the stairways `stairs-700726011` (11.2 m) and
    `stairs-744204757` (10.5 m).

  In each case the path nearest the shortest route's end is in a part of the
  network only steps connect, so the step-free route ends at the nearest
  step-free path. Crutches and reduced mobility, which may use steps, end where
  the shortest route does. The summary and the cards say where the route ends,
  and the response sends no length difference.

  The Dana Porter figure was 25 m before the acceptance pass. The step-free
  route has not moved: 567.7 m, ending 28.8 m from the library's point. The
  shortest route's end moved, to a path 24.5 m from the point instead of
  25.1 m, on the other side of a stairway, and the shortest route is now
  585.3 m with two stairways instead of 610.7 m with one. Ranking by degrees had
  put the farther path first. Both are "the nearest path" to within a metre,
  and neither is the library's door ([KI-16](../development/KNOWN_ISSUES.md)).

- **Large detour, 1.** `gradient-40585154-up`, custom profile with a 5% uphill
  limit: 3,818.9 m against 564.1 m (+3,255 m). A separate run with
  distance-only costs under the same hard limit gave 3,764.3 m, so the detour is
  forced by the declared limit. The profile's preferences add 54.6 m. That is
  what a 5% limit means on that hillside, according to the elevation model.

**No route, inspected.**

- `snap-open-field`, all six profiles: farmland more than 150 m from any mapped
  path. The refusal said "inf m" until this audit (§8).
- `snap-rim-park`, all six: RIM Park's part of the network does not connect
  ([KI-14](../development/KNOWN_ISSUES.md)). The request is refused at once.
- `stairs-1536295321` (by Columbia Icefield), wheelchair, walker, stroller and
  custom: the map joins the top and bottom only by the stairs.
- The custom 5% profile on six more journeys: `campus-pac-to-dp`,
  `campus-cif-to-fed`, `campus-b1-to-cph`, `stairs-797093095`,
  `gradient-40585154-down` and `gradient-1369397887`.

  The search is exhaustive, so every path between those points either climbs
  more than 5% somewhere on the grade or uses steps, which the custom profile,
  built on the wheelchair one, also excludes. Whether every such climb is real
  is §9's question.

Before the acceptance pass the custom profile also had no route on
`campus-mc-to-e5`, `long-dc-to-laurier`, `long-laurier-to-square` and
`long-park-to-laurier`. Each now has one because an end snaps to a nearer path:
E5's destination 18.0 m from its point instead of 21.9 m, and Laurier's 27.7 m
instead of 28.4 m.

**What the acceptance pass moved.** Fourteen of the 41 journeys' shortest
routes changed:

- On twelve, an end snapped to a different path, nearer by 0.1 to 4.9 m.
  - `campus-stj-to-ren` changed most: 417.7 m became 347.7 m, with the
    origin on a path 20.3 m from St. Jerome's point instead of 25.2 m. The old
    ranking took the farther one.
  - On `long-dc-to-laurier` the destination is 0.7 m nearer, on a path the
    shortest route reaches by a stairway. The wheelchair and walker detour
    grew from +34 m to +338 m. That route is now about stairs, not missing
    surface data.
  - `campus-mc-to-e5`, `long-laurier-to-square` and `long-park-to-laurier`
    lost a stairway from their shortest routes, and their step-free detours
    shrank, for example from +256 m to +169 m.
- `stairs-1536295321` is one 29.3 m segment with both ends on it. The route
  used to walk to the top junction and 0.1 m back. It is now the direct 29.3 m.
- `long-victoria-park-to-market` is 1.5 m longer with snap distances unchanged
  to 0.1 m. It was not inspected further.

One profile route changed on its own. `stairs-744204757` is one 30.0 m
segment with both ends on it. Reduced mobility was "+9 m to avoid 1 recorded
stairway": it was charged for the stairway twice, once per piece, and detoured.
It is now the same path as the shortest route.

**Surprising, not wrong.** `stairs-797093095` was chosen as a 41 m stairway.
The shortest route walks a 446 m loop instead, under every profile. The
stairway is tagged `access=private` on all seven of its segments, so nobody
may use it. The corpus chose it by length without reading access. It stays in
the corpus as a case of the private-access rule.

## 6. The twenty-journey corpus under policy 3

`pathable evaluate --region waterloo --algorithms --ablate` reran the fixed
corpus behind claims 3.1–3.3
([`waterloo-routes.json`](waterloo-routes.json)), standard against wheelchair:

|                                          | Policy 2 (committed until now) | Policy 3                      |
| ---------------------------------------- | ------------------------------ | ----------------------------- |
| Journeys whose wheelchair route differs  | 18 of 20                       | 17 of 20                      |
| No route at all                          | 1 (`conestoga-to-rim-park`)    | 1 (same; now refused at once) |
| Median detour, journeys that change      | 86.1 m                         | 51.5 m                        |
| Median detour, all 19 routable           | 83.8 m                         | 33.3 m                        |
| Dijkstra and A\* agree on cost           | 19 of 19                       | 19 of 19                      |
| Median nodes expanded, Dijkstra / A\*    | 7,409 / 1,970                  | 6,640 / 1,886                 |
| Ablation: no missing-gradient penalty    | changes 3                      | changes 7                     |
| Ablation: no missing-data penalty at all | changes 3                      | changes 8                     |

Eight journeys' detours changed, and every one got shorter. `boardwalk-to-uptown` is now the
shortest route (+227.7 m under policy 2), `uptown-to-transit-hub` +0.7 m
(+96.6 m), `park-to-campus` +2.5 m (+102.9 m), `campus-to-laurier` +51.5 m
(+151.9 m), `victoria-park-to-market` +26.5 m (+83.8 m),
`city-hall-to-victoria-park` +25.9 m (+69.7 m), `hospital-to-city-hall` +54.2 m
(+88.3 m) and `uptown-to-belmont` +161.1 m (+182.8 m). The verified example,
`campus-library-to-student-life`, is unchanged at +66.7 m.

Rerun on `43574b8` after the acceptance pass, every figure in the table is
unchanged, and no journey's ends moved. Three journeys' routes did move:

- `city-hall-to-market` and `victoria-park-to-market` are both 60.5 m longer
  under both profiles, with their detours unchanged. They share the Kitchener
  Market end, which now snaps to a path 33.0 m from the point instead of
  33.1 m, 60.5 m further round.
- `northfield-to-conestoga` changed by under a metre: +158.5 m became
  +159.9 m.

The wall-clock times in this file are three-run medians taken while the laptop
was paging under unrelated load. They are not a performance measurement; §10
has one.

## 7. Search

Suggestions appear as you type: after two characters and 180 ms, from the
local index only. A provider whose terms forbid per-keystroke queries, such as
public Nominatim, answers "not searched" and the field searches only when asked.
The index (gazetteer version 2, migration `0008`) adds each place's `ref` and
`short_name` from OpenStreetMap as codes, and initials PathAble generates from
the name, skipping small words and keeping digits. Spelling is folded both ways
(centre/center, theatre/theater). Ranking is deterministic: exact code, exact
name, code prefix, initials, name prefix, then the fuzzy pass. Each suggestion
says how it matched ("short code", "initials", "closest spelling").

The search-quality corpus,
[`geo/search_corpus.py`](../../services/api/src/pathable_api/geo/search_corpus.py)
version 1, is 40 queries, each naming the OpenStreetMap elements that count as
the place meant and how far down they may appear. It was written before the
ranking was tuned, and a miss is reported, not removed. Result:
[`waterloo-search-quality.json`](waterloo-search-quality.json).

| Kind of query  | Examples                                                       | Queries | Found within the allowed rank |
| -------------- | -------------------------------------------------------------- | ------- | ----------------------------- |
| Campus code    | DC, dc, SLC, MC, E5, PAC, QNC, EV3, UW, DP                     | 10      | 9                             |
| Name           | Davis, Davis Centre, University of Waterloo, Math and Computer | 13      | 13                            |
| Partial        | Davi, Student L, Conest, Physical Act, sl                      | 5       | 5                             |
| Other spelling | Davis Center, Student Life Center                              | 2       | 2                             |
| Misspelling    | Konestoga Mall, Unversity of Waterloo, Federaton Hall          | 3       | 3                             |
| Address        | 200 University Ave W, 100 Regina St S, 550 King St N           | 3       | 2                             |
| Street         | King Street North, Columbia St W, Ring Road                    | 3       | 3                             |
| Transit        | University of Waterloo station                                 | 1       | 0                             |
| **All**        |                                                                | **40**  | **37**, 34 of them first      |

Run on commit `89121e0` against the production-smoke database: median 96.6 ms
a query, slowest 204 ms, on a laptop under heavy unrelated load.

The three misses, inspected:

- **DP** for the Dana Porter Library. OpenStreetMap records its code as LIB, and
  DP is campus slang. The authoritative UW building list that would carry it is
  unlicensed, and the API that serves it forbids bulk retrieval
  ([KI-15](../development/KNOWN_ISSUES.md)). No hand-written alias was added.
- **550 King St N** for Conestoga Mall. The shops that share the mall's address
  rank above the mall itself. Not tuned for one query.
- **University of Waterloo station**. The top two results are the station's
  other OpenStreetMap elements, named the same; the corpus named one node. The
  expectation was too narrow, and it is left as written rather than edited
  after the result.

"DC" also suggests a restaurant whose OpenStreetMap short name is DC, second,
below the Davis Centre. The note under each says "short code", not "building
code", because restaurants carry them too.

## 8. Bugs found and fixed while building this

Each has a regression test except the last, a command-line label.

| Found by                           | Bug                                                                                                                          | Fix                                                                     |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Real-route probe                   | A crossing drawn in several segments paid a crossing penalty per segment, and its middle read as "no kerb recorded".         | One crossing is one unit (§2).                                          |
| Real-route probe                   | A split stairway's step count was charged in full on every piece.                                                            | The stairway is one unit.                                               |
| Real-route probe                   | Counts shown to people counted segments: "4 stairways" for three.                                                            | Counts are of units.                                                    |
| Task card, confirmed on the corpus | The shortest route was re-timed at the selected profile's pace.                                                              | Always walking pace (§1).                                               |
| Truth audit, first run             | Columbia Icefield to Federation Hall had no route for anybody: `access=private` segments counted as connections.             | Parts count walkable segments only.                                     |
| Truth audit, first run             | PAC to Dana Porter had no step-free route.                                                                                   | Step-free parts and re-snapping (§3).                                   |
| Truth audit, second run            | A step-free route of 0 m, "29 m shorter", up a stairway.                                                                     | A moved end stays nearer its own point.                                 |
| Truth audit, second run            | "43 m shorter" for a route that ends 25 m elsewhere.                                                                         | The headline says where it ends.                                        |
| Truth audit, review                | "The origin is inf m from the nearest mapped path." Present on `main`.                                                       | "more than 150 m"; the audit now checks every sentence for inf and nan. |
| CI integration tests               | Same-named results of different kinds were merged, losing Waterloo Public Square station.                                    | Deduplicate by kind, label and category.                                |
| Planner, real data                 | The route card said "43 m shorter" beside "Ends 25 m away".                                                                  | Cards say where the route ends.                                         |
| Planner, real data                 | The phone card said "2 counts missing" about one stairway.                                                                   | Counted per stairway.                                                   |
| Planner, real data                 | A route with no surface tag anywhere was labelled both recorded and not, "some segments … (0% recorded)". Present on `main`. | Labelled not recorded.                                                  |
| Planner, real data                 | "DC" suggested a restaurant as a "building code".                                                                            | "short code".                                                           |
| Route evaluation                   | A journey with no route printed as "[same]".                                                                                 | "[no route]".                                                           |
| Acceptance review                  | Closed paths within 60 m hid an open one 60–150 m away; the search ranked candidates by degrees.                             | Widen until the best lies within the window; rank by metres (§10).      |
| Acceptance review                  | Two ends on one segment were joined only through its junctions: 200.3 m for an 80 m walk.                                    | One chain through every snap on a segment (§10).                        |
| Acceptance review                  | A piece of a crossing or stairway paid the whole segment's flat penalties; two ends on one paid twice.                       | A piece carries the share its length covers (§10).                      |
| Acceptance review                  | Routes that end apart still carried `extra_distance_m` and a "shorter than the shortest route" explanation.                  | `distance_comparable: false`, no difference sent (§10).                 |
| Acceptance review                  | At 1366 × 768 the dock under the panel left 460 px; a moved-end headline pushed the shortest card below the fold.            | The dock sits beside the panel on short windows (§10).                  |

## 9. What is still not defensible

- **The cost weights are engineering judgement.** Policy 3 removes charges the
  data did not support; it does not validate the charges that remain. Nobody
  who uses a wheelchair, walker or crutches has judged a route.
- **The stricter one-kerb rule lengthens some routes.** A crossing with one
  recorded kerb is now unknown. On the five-profile probe, crutches on
  `victoria-park-to-market` went from +26.8 m to +41.1 m. It is the cautious
  reading, not a measured one.
- **Paces are assumptions**, written as ranges and labelled, not measurements of
  anybody.
- **Routes end at the nearest usable path, not at a door.** A moved end is
  stated, but no route knows where a building's accessible entrance is
  ([KI-16](../development/KNOWN_ISSUES.md)).
- **Campus codes are OpenStreetMap's.** DP is not found, and the authoritative
  UW list needs the university's permission ([KI-15](../development/KNOWN_ISSUES.md)).
- **The custom 5% uphill limit refuses 7 of 41 journeys,** including campus
  ones. It refused 11 before the acceptance pass; four gained a route only
  because an end now snaps to a nearer path. A hard limit over derived grade is
  only as good as 1 m elevation on short segments; whether those refusals match
  the ground is not established.
- **The truth audit checks the product against its own data, not against the
  street.** Its corpus was chosen by the author, and nobody has walked a route.
  [`FIELD_VERIFICATION_CHECKLIST.md`](FIELD_VERIFICATION_CHECKLIST.md) is the
  first bounded check; none of it has been done.
- **"The nearest path" is fragile at building scale.** Ranking snaps by metres
  moved twelve of the audit's 41 shortest routes, one by 70 m, and changed what
  some step-free comparisons are about (§5). Every new end is nearer the point
  asked for. None is a door ([KI-16](../development/KNOWN_ISSUES.md)).
- **Performance is one laptop's, under memory pressure** (§10). A\* expands
  about a fifth of Dijkstra's nodes, but its wall-clock advantage is within the
  run-to-run noise. Graph load is 4–13 s slower than on `main` on this laptop,
  mostly the connected-part passes, and has not been measured on the pilot's
  two-vCPU host.
- **Walking one-ways are tested only on invented networks.** Waterloo's data
  has no `oneway:foot`, so two ends on a one-way segment are proven by unit
  tests, not on real data.
- **The live pilot still runs policy 2.** `infra/vps/smoke.py` now expects
  401.3 m with 3 stairways against 503.7 m, so it fails against the pilot until
  this is deployed, which is the founder's decision. _(2026-11-03: deployed as
  `629a67a`; the smoke passed 19/19 from outside the host and on it.)_
- **At 1280 × 720** a headline naming two moved ends still cuts the shortest
  route card's last line, its barrier chips; the panel scrolls. At 1366 × 768
  and 1280 × 800 both cards show (§10).

## 10. Acceptance pass

After review, one focused pass on 2026-11-02. Each fault below was reproduced
by a test that failed before the fix, and every real-data record in this file
was then regenerated on commit `43574b8`.

**Closed paths hid open ones.** Snapping widens its search through windows of
60, 200 and 500 m. It returned the first window that held any segment, usable
or not. A closed cycleway 20 m away filled the 60 m window, every candidate was
refused, and an open footway 100 m away, inside the 150 m limit, was never
looked at: "off the network". The same search decides where a step-free end
moves to, so stairs within 60 m hid a step-free path 90 m away. Its first look
also took the nearest segment in degrees, and a window's best was trusted
however far away it lay. A degree east is about 73% of a degree north here, so
an open path 70 m north beat one 62 m east. Now candidates are gathered in a
window sized from the point's own latitude and ranked by geodesic metres. A
window's best is trusted only within the window's radius; otherwise the window
widens, until it covers the 150 m limit. On real data this moved twelve of the
audit's shortest routes (§5).

**Two ends on one segment.** Each end cut the whole segment on its own, which
joined each point to the segment's junctions but not to each other. The route
between them walked to a junction and back: 200.3 m for an 80 m walk in either
direction, and 200.3 m for two ends at one point. Snaps on one segment now cut
it into one chain, junction to junction through every point in order, linked
only in the directions the segment may be walked. A one-way stretch is direct
in its own direction, is never walked backwards, and gives no route when there
is no way round. On real data: `stairs-1536295321` (§5).

**A piece of a crossing or stairway.** A piece of a segment kept the whole
segment's `unit_share`. Half of a 12 m crossing paid the whole crossing and
kerb penalty, half a stairway the whole step charge, and two ends on one
crossing paid for it twice. A piece now carries the share its length covers,
so the two sides of a snap add up to the segment. Hard limits read what a piece
is, not its share: a 10% piece of a stairway is still a stairway to a wheelchair
profile, and a piece of a 9% segment is still too steep for a 5% limit. A\* and
Dijkstra agree on routes that start part way across. On real data:
`stairs-744204757` under reduced mobility (§5).

**Lengths of different journeys.** When the profile route had to end
elsewhere, the cards and headline said "Ends 25 m away", but the response still
carried `extra_distance_m: -43` and an explanation reading "43 m shorter than
the shortest route". The comparison now says whether the two lengths are of
one journey. When the ends are more than 5 m apart, `distance_comparable` is
false, `extra_distance_m` and `extra_distance_fraction` are null, and the
distance explanation is `distance_not_compared`, which says where the route
goes instead. The moved-end caution and headline are unchanged. The route
evaluation gives such a journey no detour. The truth audit measures the ends
itself, and treats a difference reported for routes that end apart as a
defect, and a missing one for routes that end together.

**The comparison at 1366 × 768.** On a window shorter than the design frames,
the comparison's dock sat under the panel. Measured in the real planner against
the local API, with the same route data before and after:

| Window     | Panel shows, before → after | Shortest card ends at       | Before      |
| ---------- | --------------------------- | --------------------------- | ----------- |
| 1366 × 768 | 460 → 556 px                | 507 px (PAC → Dana Porter)  | 47 px under |
| 1366 × 768 | 460 → 556 px                | 468 px (Davis Centre → SLC) | 8 px under  |
| 1280 × 720 | 412 → 508 px                | 507 px (PAC → Dana Porter)  | hidden      |
| 1536 × 864 | 572 → 668 px                | 507 px                      | fits        |
| 1440 × 900 | 592 → 688 px                | 507 px                      | fits        |

The dock now sits beside the panel on a short window, as it already did for the
evidence layout. A browser test stubs the longest headline the API writes,
both ends moved, at 1366 × 768 and 1280 × 800. With the dock rule reverted it
fails at both. At 1280 × 720 that headline still cuts the shortest card's last
line (§9).

**Performance under policy 3.** Measured with the new
`pathable benchmark corpus` on the twenty-journey corpus
([`waterloo-performance-policy3.json`](waterloo-performance-policy3.json)):

- **Machine.** Intel Core Ultra 7 155H (22 logical processors), 16 GB, Windows
  11, Python 3.13.2. Commit `43574b8`, dataset `51585450…`.
- **Conditions.** 630 MB of memory was available before the load, and Windows
  trimmed the process to 298 MB by the end, so it was paging.
- **Method.** Warm and single process. The spatial index was built and timed
  apart, in 227 ms, and every journey was routed once per profile and
  algorithm before timing. Each timed run is one `compute_route`: snapping,
  the request-local split, the search and building the route. Dijkstra and A\*
  alternate within each of 5 repeats. The figures are percentiles over
  per-journey medians.

| Profile, algorithm   | Routable | p50      | p95        | Median nodes expanded |
| -------------------- | -------- | -------- | ---------- | --------------------- |
| Standard, Dijkstra   | 19       | 249.5 ms | 1,267.8 ms | 7,984                 |
| Standard, A\*        | 19       | 168.7 ms | 513.5 ms   | 807                   |
| Wheelchair, Dijkstra | 19       | 269.6 ms | 1,562.4 ms | 6,640                 |
| Wheelchair, A\*      | 19       | 282.7 ms | 2,512.1 ms | 1,886                 |

- **Agreement.** Dijkstra and A\* agree on cost for all 40 journey-profile
  pairs: 38 identical optimal costs, and 2 where neither finds a route (RIM
  Park).
- **Both profiles together.** A\* expands 1,398 nodes at the median against
  Dijkstra's 7,041. Its wall-clock p50 is 219.6 ms against 266.5 ms, and it is
  faster on 23 of 38 pairs. Repeats of one journey varied by a factor of 1.74
  at the median and up to 11.6, so the wall-clock difference is not
  established. The work saved is.
- **A whole comparison** (both routes and the explanation, wheelchair, A\*, no
  HTTP or JSON) takes 458.8 ms at p50, 1,595.1 ms at p95 and 2,281.3 ms at
  most, over 20 journeys.
- **Memory.** 810 MB working set after the load, 1,019 MB at peak.

Graph load, from `pathable benchmark load`
([`waterloo-graph-load-policy3.json`](waterloo-graph-load-policy3.json)):

- **This branch.** Median 67.3 s over three timing runs (60.3–69.8 s), two of
  them under memory pressure. Peak Python heap 745.8 MB under tracemalloc.
  Working set 709 MB after a load, 911.7 MB at peak.
- **Against `main`.** On the same machine within ten minutes, alternating
  ([`waterloo-graph-load-core-v2-ab.json`](waterloo-graph-load-core-v2-ab.json)):

  | Load              | `main` `e62b7e8` | This branch `43574b8` |
  | ----------------- | ---------------- | --------------------- |
  | First, elapsed    | 29.2 s           | 41.9 s                |
  | Second, elapsed   | 31.3 s           | 35.4 s                |
  | Working set after | 803 / 816 MB     | 863 / 862 MB          |
  | Peak working set  | 1,073 / 1,054 MB | 1,128 / 1,199 MB      |

  Core v2 costs 4–13 s more per load on this laptop, 8 s on average over the
  two pairs, and about 50 MB more resident memory. 7–9 s of that is the two
  connected-part passes (§3), which run after the loader's own timer stops.

Policy 2's figures (claims 3.4 and 3.5) came from a one-off script with a
different method and are not compared with these.

**The truth audit and the gates.** The audit reran on `43574b8` with no
defect (§5). The field checklist is
[`FIELD_VERIFICATION_CHECKLIST.md`](FIELD_VERIFICATION_CHECKLIST.md); nothing in
it has been performed.

## 11. Reproduce

Against the production-smoke database (`docs/deployment/PRODUCTION_SMOKE.md`),
from `services/api`:

```sh
pathable truth-audit --region waterloo --json waterloo-truth-audit.json
pathable gazetteer evaluate --region waterloo --json waterloo-search-quality.json
pathable evaluate --region waterloo --profile wheelchair --algorithms --ablate --json waterloo-routes.json
pathable benchmark corpus --region waterloo --repeats 5 --json waterloo-performance-policy3.json
pathable benchmark load --region waterloo --runs 3 --heap-runs 1 --json waterloo-graph-load-policy3.json
```

The audit and both benchmarks record the commit they ran on, and `-dirty` if
the tree had uncommitted changes. Run one at a time: each loads the graph.
