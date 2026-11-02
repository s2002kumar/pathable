# Waterloo Core v2 — evidence

What changed in routing, timing, search and the comparison view, and what real
Waterloo data showed while it was built. Every figure below came from a run on
the real network: dataset `51585450…` (180,554 segments, OpenStreetMap as of
2026-08-16, 1 m HRDEM elevation) and its place index, read from the same
extract (`cee90e37…`). Runs were made on a development laptop under heavy load
from unrelated work, so timings here are upper bounds, not performance claims.

Machine-readable records:

- [`waterloo-truth-audit.json`](waterloo-truth-audit.json): every case,
  profile, check and flag of the product truth audit.
- [`waterloo-search-quality.json`](waterloo-search-quality.json): every query
  of the search-quality corpus and what came back.
- [`waterloo-routes.json`](waterloo-routes.json): the twenty-journey corpus,
  regenerated under policy 3 (§6).

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
stairways" now says 3. The routes themselves did not move: 287.4 m and 406.4 m
are unchanged.

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

When an end has moved, the comparison leads with it: "Ends 25 m away: the map
has no path for this profile to where the shortest route ends." Two routes that
end in different places are not one journey made two ways, so the cards say
"Ends 25 m away" instead of setting their lengths against each other, and a
caution says the same in a sentence.

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
  whose ends differ; an explanation pointing at a segment the chosen route
  uses; a time that is not distance over the stated pace; a response that
  disagrees with the objects behind it; "inf" or "nan" in a sentence.
- **Flags**, which a person inspects: a long detour around missing data rather
  than a recorded barrier, a profile route shorter than the shortest route, a
  detour over 300 m and 50%, a snap over 50 m from the point asked for, ends
  that moved, and a different route with no explanation.

Run on commit `89121e0`, routing policy 3, truth audit and corpus version 1:

|                                                 |                                                 |
| ----------------------------------------------- | ----------------------------------------------- |
| Journeys                                        | 41                                              |
| Profile comparisons                             | 246                                             |
| Compared, both routes found                     | 220: 59 on the same path, 161 on different ones |
| No route for that profile                       | 14                                              |
| No route for anybody                            | 12                                              |
| Shortest route identical under all six profiles | 39 of 39 journeys that have one                 |
| **Defects**                                     | **0**                                           |
| Flags                                           | 18, each inspected below                        |

**Flags.**

- **Ends moved, 14.** On four journeys the destination moved for the three
  step-free profiles (wheelchair, walker, stroller), and on the two stairways
  for the custom profile too:
  - Physical Activities Complex to the Dana Porter Library: 25 m. The library's
    nearest path is reached only by a stairway.
  - Columbia Icefield to Federation Hall: 43 m.
  - The tops of the stairways `stairs-700726011` (11 m, also the custom
    profile) and `stairs-744204757` (10.5 m, also the custom profile).

  In each case the path nearest the shortest route's end is in a part of the
  network only steps connect, so the step-free route ends at the nearest
  step-free path. Crutches and reduced mobility, which may use steps, end where
  the shortest route does. The summary and the cards say where the route ends.
  Rendered over the basemap, the two campus routes end beside the same
  building as the shortest route; the stairway cases were not rendered.

- **Profile route shorter, 3.** These are the PAC to Dana Porter comparisons
  above: the step-free route is 43 m shorter because it ends 25 m elsewhere.
  This is not a shorter way to the same point, and no screen says it is.
- **Large detour, 1.** `gradient-40585154-up`, custom profile with a 5% uphill
  limit: 3,818.9 m against 564.1 m. A separate run with distance-only costs
  under the same hard limit gives 3,764.3 m, so the detour is forced by the
  declared limit. The profile's preferences add 54.6 m. That is what a 5%
  limit means on that hillside, according to the elevation model.

**No route, inspected.**

- `snap-open-field`, all six profiles: farmland more than 150 m from any mapped
  path. The refusal said "inf m" until this audit (§8).
- `snap-rim-park`, all six: RIM Park's part of the network does not connect
  ([KI-14](../development/KNOWN_ISSUES.md)). The request is refused at once.
- `stairs-1536295321` (by Columbia Icefield), wheelchair, walker, stroller and
  custom: the map joins the top and bottom only by the stairs.
- The custom 5% profile on ten journeys:
  - `campus-mc-to-e5`, `campus-pac-to-dp`, `campus-cif-to-fed` and
    `campus-b1-to-cph`;
  - `long-dc-to-laurier`, `long-laurier-to-square` and `long-park-to-laurier`;
  - `stairs-797093095`, `gradient-40585154-down` and `gradient-1369397887`.

  The search is exhaustive, so every path between those points either climbs
  more than 5% somewhere on the grade or uses steps, which the custom profile,
  built on the wheelchair one, also excludes. Whether every such climb is real
  is §9's question.

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

The wall-clock times in the file were taken while the laptop was paging under
unrelated load, and A\* came out slower than Dijkstra on 14 of 19 journeys.
They are not a performance measurement; claims 3.4 and 3.5 remain policy 2's.

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
- **The custom 5% uphill limit refuses 10 of 41 journeys,** including campus
  ones. A hard limit over derived grade is only as good as 1 m elevation on
  short segments; whether those refusals match the ground is not established.
- **The truth audit checks the product against its own data, not against the
  street.** Its corpus was chosen by the author, and nobody has walked a route.
- **Performance under policy 3 is unmeasured.** Claims 3.4 and 3.5 are policy
  2's, and the timings in this run were taken on a paging laptop.
- **The live pilot still runs policy 2.** `infra/vps/smoke.py` now expects
  policy 3's answer (406.4 m with 3 stairways, 503.4 m), so it fails against the
  pilot until this is deployed, which is the founder's decision.
- **On a 1366 × 768 laptop**, the moved-end headline is long enough to push the
  shortest-route card partly below the panel's fold; it is reached by
  scrolling the panel.

## 10. Reproduce

Against the production-smoke database (`docs/deployment/PRODUCTION_SMOKE.md`),
from `services/api`:

```sh
pathable truth-audit --region waterloo --json waterloo-truth-audit.json
pathable gazetteer evaluate --region waterloo --json waterloo-search-quality.json
pathable evaluate --region waterloo --profile wheelchair --algorithms --ablate --json waterloo-routes.json
```

Each records the commit it ran on, and `-dirty` if the tree had uncommitted
changes.
