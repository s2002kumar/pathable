# Field verification checklist — eight Waterloo journeys

**Status: not performed.** Nobody has walked, rolled or measured any of these
journeys for PathAble. Until a row below is filled in from a visit, nothing in
this file is evidence, and nothing may be described as field-verified.

This is the bounded first check of whether what PathAble says on real Waterloo
journeys matches the ground. It checks map facts at named places — a kerb, a
stairway, a slope, an approach to a door — not the cost weights, and not
whether a route is "accessible", which PathAble never claims.

## What the eight journeys cover

| #   | Journey                                            | Profile    | Kerbs | Stairs | Entrance | Slope | Alternative path |
| --- | -------------------------------------------------- | ---------- | :---: | :----: | :------: | :---: | :--------------: |
| 1   | Uptown square to the transit hub                   | Wheelchair |   ✓   |        |          |       |        ✓         |
| 2   | Victoria Park to Kitchener Market                  | Crutches   |   ✓   |        |          |   ✓   |                  |
| 3   | Verified example: Davis Centre library to SLC      | Wheelchair |       |   ✓    |    ✓     |       |        ✓         |
| 4   | Searched journey: Davis Centre Library to SLC      | Wheelchair |   ✓   |   ✓    |    ✓     |       |        ✓         |
| 5   | Physical Activities Complex to Dana Porter Library | Walker     |   ✓   |   ✓    |    ✓     |   ✓   |        ✓         |
| 6   | Steep path in south Kitchener, uphill              | Wheelchair |       |        |          |   ✓   |                  |
| 7   | Modern Languages to South Campus Hall              | Wheelchair |       |        |          |   ✓   |        ✓         |
| 8   | Campus stairway, 71 m, bottom to top               | Wheelchair |       |   ✓    |          |   ✓   |        ✓         |

Journeys 3 and 4 share the same three stairways, and journey 1's King Street
crossing is the truth audit's `crossing-1105419114-no-kerbs` case.

## Where the figures come from

Every figure, coordinate and node id below is what the local API answered under
routing policy 3 on 2026-11-02, on dataset `51585450-8ff5-409d-a02e-66d5a3c5e260`
(OpenStreetMap, Waterloo region). The journey keys are those of
[`truth_corpus.py`](../../services/api/src/pathable_api/routing/truth_corpus.py),
so `pathable truth-audit` re-routes the same points; journey 3 is the landing
page's [`verified-route.ts`](../../apps/web/src/features/landing/verified-route.ts).
Coordinates are latitude, longitude. OSM nodes open at
`https://www.openstreetmap.org/node/<id>`.

If the dataset or routing policy changes before the visit, re-route the journey
first and update the figures: a check against a route PathAble no longer gives
tests nothing.

## How to record a check

For every box, record:

- **When and who**: date, time, and the observer's name or initials.
- **What was there**: the observed value, using the measure the check names.
  Heights are in centimetres, from a tape or ruler held vertical at the
  kerb's lip. A slope is three readings with a digital level or phone
  inclinometer laid along the direction of travel. Steps are counted.
- **Evidence**: at least one photo per check, taken from the footway.
- **Verdict**: **matches**, **contradicts** or **cannot tell**, against the
  "PathAble says" line. "Cannot tell" is a valid answer and is not a match.

Classify kerbs with the definitions on the OpenStreetMap wiki's `Key:kerb`
page, and record the measured height as well, so a reader can reclassify.

**Safety and privacy.** Measure kerbs from the footway or a signalled refuge.
Never stand in a live traffic lane. Visit in daylight. Do not photograph
people's faces or vehicle plates. Do not photograph anyone using a mobility aid
without their consent.

## What a result does and does not establish

- A **match** says one OSM fact was true at one place on one day. It does not
  validate a route, a cost weight or the absence of barriers nobody recorded.
- A **contradiction** is a finding: record it as a known issue with the photo.
  Correcting OpenStreetMap from a survey is legitimate, but it is a separate
  decision.
- A derived grade is an average over a whole segment from a 1 m terrain model.
  A short, steep reading inside a gentler segment, or the reverse, is expected,
  and should be recorded rather than averaged away.
- None of this is external validation in Gate E's sense unless someone outside
  this repository performs it.

---

## 1. Uptown square to the transit hub — wheelchair

`crossing-uptown-to-hub`: (43.4650, −80.5222) to (43.4664, −80.5228).

**PathAble says:** "+1 m to avoid 12 m of path with no recorded surface
condition." Shortest 182.3 m, wheelchair 183.0 m. Each route crosses three
signalised crossings recorded as lowered at both ends. Both cross King Street
North on a marked crossing with no kerb recorded at either end.

- [ ] **King Street North marked crossing** at 43.466071, −80.522704 (nodes
      10115733721, 10115733722, 6865304497). PathAble says: no kerb recorded at
      either end, so unknown. Record each end's kerb height and type.
- [ ] **Signalised crossings on the wheelchair route** at 43.465100, −80.522221
      (nodes 8060737277, 8481245380); 43.465228, −80.522250 (nodes 6091347592,
      6872489585, 8481245382, 8481245383); 43.465304, −80.522401 (nodes 6872487579,
      8060737272–8060737274). PathAble says: lowered at both ends. Check each end.
- [ ] **Signalised crossings on the shortest route** at 43.465075, −80.522399;
      43.465202, −80.522501; 43.465288, −80.522496. PathAble says: lowered at both
      ends. Check each end.
- [ ] **Alternative path.** Walk both routes. Record any step, kerb, gate or
      surface problem on either that the map does not show.

## 2. Victoria Park to Kitchener Market — crutches

`long-victoria-park-to-market`: (43.446418, −80.498274) to (43.446358, −80.483738).

**PathAble says:** "+20 m for gentler gradients." Shortest 1,512.6 m, crutches
1,533.0 m. The origin snaps 16.0 m from the requested point.

- [ ] **Unmarked crossing with no kerb recorded** at 43.445694, −80.495596
      (nodes 1463004904, 8694896718), on both routes. Record each end.
- [ ] **Joseph Street marked crossing with no kerb recorded** at 43.448497,
      −80.491816 (nodes 10133893874, 9191317994, 9194882938), on the shortest
      route only. Record each end.
- [ ] **Steepest climb on the shortest route**: 5.76% estimated over 30.1 m at
      43.448369, −80.488647 (nodes 7837713061, 8295118559), concrete, with no
      recorded incline. Measure the slope in the direction of travel.
- [ ] **Steepest climb on the crutches route**: 5.3% estimated over 25.1 m at
      43.448456, −80.488578 (nodes 1475099754, 7837713053), asphalt. Measure it.
      The route's whole reason is that this climb is gentler; record whether it is.

## 3. Verified example: Davis Centre library to the Student Life Centre — wheelchair

Landing page record: (43.4728, −80.5424) to (43.4715, −80.5449).

**PathAble says:** "+67 m to avoid 3 recorded stairways." Shortest 287.4 m
with three stairways, wheelchair 354.1 m with none. Both end 15.3 m from the
requested destination, at 43.471367, −80.544851.

- [ ] **Stairway, 5 recorded steps**, at 43.472359, −80.542835 (nodes
      1505832303, 1505832399). Count the steps; note any handrail or ramp beside it.
- [ ] **Stairway, step count not recorded**, at 43.471954, −80.543423 (nodes
      1951555104, 1951555165, 1951555169). Count the steps.
- [ ] **Stairway, 11 recorded steps**, at 43.471690, −80.544077 (nodes
      1932767867, 1976914988). Count the steps.
- [ ] **The wheelchair route is step-free end to end.** Walk or roll it. Record
      any step, threshold, door or kerb on it. This is the claim the landing page
      makes, and the one most worth contradicting if it is wrong.
- [ ] **Entrance approach.** From where both routes end, 15.3 m from the
      requested point, find the nearest door of the Student Life Centre. Record
      whether the way to it is step-free, and how far it is.

## 4. Searched journey: Davis Centre Library to the Student Life Centre — wheelchair

`campus-dc-to-slc`, the journey `infra/vps/smoke.py` searches for:
(43.472428, −80.541997) to (43.471972, −80.545213).

**PathAble says:** "+102 m to avoid 3 recorded stairways." Shortest 401.3 m
over the same three stairways as journey 3; wheelchair 503.8 m with none.

- [ ] **The wheelchair route is step-free end to end.** It differs from
      journey 3's. Record any step, threshold, door or kerb on it.
- [ ] **Unmarked crossing recorded as lowered at both ends** at 43.473561,
      −80.542046 (nodes 1450232815, 8307684710, 8307684714). Check each end.
- [ ] **Steepest climb on the wheelchair route**: 4.15% estimated over 16.2 m
      at 43.473066, −80.541622 (nodes 1976894147, 1976894119), paving stones.
      Measure it.
- [ ] **Entrance approach.** Both routes end 1.2 m from the requested point.
      Record the step-free way from there to the nearest Student Life Centre door,
      if there is one.

## 5. Physical Activities Complex to Dana Porter Library — walker

`campus-pac-to-dp`: (43.472406, −80.546114) to (43.469733, −80.542271).

**PathAble says:** "Ends 52 m away: the map has no path for this profile to
where the shortest route ends." Shortest 585.3 m with two stairways, ending at
43.469638, −80.542545. Walker route 567.7 m, ending at 43.469930, −80.542040,
28.8 m from the requested point. The two lengths are not compared.

- [ ] **Stairway near Dana Porter, step count not recorded**, at 43.469611,
      −80.542623 (nodes 1999591897, 1999591925), on the shortest route's way to its
      end. Count the steps, and record whether any step-free way bypasses it. The
      map has no step-free path to the shortest route's end, which is why the
      walker route ends elsewhere.
- [ ] **Stairway, step count not recorded**, at 43.471407, −80.544594 (nodes
      3725159562, 3725159563). Count the steps.
- [ ] **Uncontrolled crossing with no kerb recorded** at 43.470319, −80.544262
      (nodes 5502132895–5502132897), on the shortest route. Record each end.
- [ ] **Steepest climb on both routes**: 8.33% estimated over 43.9 m at
      43.472874, −80.545686 (nodes 1436010067, 1607240665), surface not recorded.
      Measure the slope and record the surface.
- [ ] **Entrance approach.** From where the walker route ends, find Dana
      Porter's nearest usable entrance. Record whether a walker can reach it
      without steps, and how. This is the gap PathAble's moved end describes; see
      KI-16.

## 6. Steep path in south Kitchener, uphill — wheelchair

`gradient-40585154-up`: (43.438097, −80.531191) to (43.437598, −80.537267).

**PathAble says:** "+234 m for gentler gradients." Shortest 564.1 m, wheelchair
797.7 m. Neither has a recorded incline; every gradient here is estimated from
the terrain model.

- [ ] **Steepest climb on the shortest route**: 16.35% estimated over 8.5 m at
      43.437598, −80.536960 (nodes 388003348, 388003349), fine gravel. Measure it.
      A segment this short is where a terrain model is least reliable.
- [ ] **Steepest climb on the wheelchair route**: 11.15% estimated over 14.1 m
      at 43.436644, −80.534909 (nodes 388003440, 388003441), fine gravel. Measure
      it. If it is as steep as estimated, the wheelchair route is still steep, and
      the "gentler" alternative should be described as such.
- [ ] **Surface.** Both climbs are recorded as fine gravel. Record what is
      there and its condition.

## 7. Modern Languages to South Campus Hall — wheelchair

`campus-ml-to-sch`: (43.468984, −80.542737) to (43.469212, −80.540208).

**PathAble says:** "Same path as shortest route." 272.2 m. No segment on the
route has a recorded surface.

- [ ] **Steepest climb**: 9.29% estimated over a 2.0 m segment at 43.469009,
      −80.542721 (nodes 11304345811, 3872192573), next to the start. Record what is
      there. A 2 m climb this steep can be a ramp, a kerb or a step, and the map
      records none of them.
- [ ] **Surface.** Record the surface along the route. PathAble has none on
      record.
- [ ] **Alternative path.** "Same path" means the wheelchair profile found no
      better way on the map. Record whether a better way exists on the ground.

## 8. Campus stairway, 71 m, bottom to top — wheelchair

`stairs-700726011`: (43.471175, −80.549346) to (43.471515, −80.549858).

**PathAble says:** "Ends 11 m away: the map has no path for this profile to
where the shortest route ends." Shortest 71.4 m up the stairway. The step-free
route is 353.2 m and ends 11.2 m from the top.

- [ ] **The stairway** at 43.471318, −80.549721 (nodes 1436010165,
      6580409239–6580409244, 7643481650), 71.3 m long, step count not recorded.
      Count the steps and flights. Note handrails and landings.
- [ ] **The step-free route.** Roll or walk it. Record any step, kerb or
      surface problem. Its steepest climb is 4.73% estimated over 12.4 m at
      43.472079, −80.548427 (nodes 1436010059, 1436010150), asphalt. Measure it.
- [ ] **Where it ends.** Record whether the top of the stairway can be reached
      step-free from where the route ends, 11.2 m away.
