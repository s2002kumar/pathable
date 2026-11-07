# Field verification checklist — eight Waterloo journeys

**Status: not performed.** Nobody has walked, rolled or measured any of these
journeys for PathAble. Until a row of
[`field-verification-record.csv`](field-verification-record.csv) is filled in
from a visit, nothing here is evidence, and nothing may be described as
field-verified. Performed by the founder, it is a field check of map facts, not
user validation; and it is not external validation in Gate E's sense unless
somebody outside this repository performs it.

It checks map facts at named places — a kerb, a stairway, a slope, the way to a
door — not cost weights, and not whether a route is "accessible", which
PathAble never claims.

## Before you go

- **Bring:** a tape measure (cm), a phone with an inclinometer or a digital
  level, the phone camera, and the recording sheet
  ([`field-verification-record.csv`](field-verification-record.csv): one row per
  check, opens in any spreadsheet app) or a printout of this page.
- **Plan by area.** Journeys 3, 4, 5, 7 and 8 are all on the University of
  Waterloo campus and fit one visit; journey 1 is uptown Waterloo; journey 2 is
  downtown Kitchener; journey 6 is a gravel path to the south.
- **Re-route first if anything changed.** The figures are what the API answered
  under routing policy 3 on 2026-11-02, on dataset
  `51585450-8ff5-409d-a02e-66d5a3c5e260`. Before going, compare that with
  `dataset.dataset_id` and `routing_policy_version` in any live route response
  from the pilot. If either differs, re-route the journey
  (`pathable truth-audit`; keys from
  [`truth_corpus.py`](../../services/api/src/pathable_api/routing/truth_corpus.py))
  and update the figures: a check against a route PathAble no longer gives tests
  nothing.
- **Find each place** by its map link (OpenStreetMap, zoomed to the point) or its
  nodes (`https://www.openstreetmap.org/node/<id>`). The "Near" line names the
  closest place and street in PathAble's own place index, as a landmark; it is
  not a description of the check.

## How to record a check

For each check, fill its row in the sheet:

| Column                     | What goes in it                                                                 |
| -------------------------- | ------------------------------------------------------------------------------- |
| `result`                   | **match**, **mismatch** or **unable to determine**, against "PathAble claims"   |
| `observed`                 | What was there, in the measure the check asks for                               |
| `notes`                    | Anything else: weather, a temporary obstruction, why it could not be determined |
| `photo_files`              | Photo file names, e.g. `F3.1-a.jpg F3.1-b.jpg` (the check id, then a letter)    |
| `observer`, `date`, `time` | Who checked, and when                                                           |

"Unable to determine" is a valid result and is not a match. Measure kerb heights
in centimetres with the tape held vertical at the kerb's lip, and classify them
on the OpenStreetMap wiki's `Key:kerb` scale as well, so a reader can reclassify.
Take a slope as three readings laid along the direction of travel. Count steps.

**Safety and privacy.** Measure kerbs from the footway or a signalled refuge,
never in a live traffic lane. Visit in daylight. Do not photograph people's
faces or vehicle plates, or anyone using a mobility aid without their consent.

## What a result does and does not establish

- A **match** says one OSM fact was true at one place on one day. It does not
  validate a route, a cost weight or the absence of barriers nobody recorded.
- A **mismatch** is a finding: record it as a known issue with its photo.
  Correcting OpenStreetMap from a survey is legitimate, but a separate decision.
- A derived grade is an average over a whole segment of a 1 m terrain model. A
  short steep reading inside a gentler segment, or the reverse, is expected; record
  it rather than average it away.

## The eight journeys

| #   | Journey                                                           | Profile    | Checks | Kerbs | Stairs | Slope | Entrance | Whole route |
| --- | ----------------------------------------------------------------- | ---------- | -----: | :---: | :----: | :---: | :------: | :---------: |
| 1   | Uptown square to the transit hub                                  | Wheelchair |      8 |   ✓   |        |       |          |      ✓      |
| 2   | Victoria Park to Kitchener Market                                 | Crutches   |      4 |   ✓   |        |   ✓   |          |             |
| 3   | Verified example: Davis Centre library to the Student Life Centre | Wheelchair |      5 |       |   ✓    |       |    ✓     |      ✓      |
| 4   | Searched journey: Davis Centre Library to the Student Life Centre | Wheelchair |      4 |   ✓   |        |   ✓   |    ✓     |      ✓      |
| 5   | Physical Activities Complex to Dana Porter Library                | Walker     |      5 |   ✓   |   ✓    |   ✓   |    ✓     |             |
| 6   | Steep path in south Kitchener, uphill                             | Wheelchair |      3 |       |        |   ✓   |          |      ✓      |
| 7   | Modern Languages to South Campus Hall                             | Wheelchair |      3 |       |        |   ✓   |          |      ✓      |
| 8   | Campus stairway, 71 m, bottom to top                              | Wheelchair |      3 |       |   ✓    |   ✓   |    ✓     |             |

Journeys 3 and 4 share the same three stairways, and journey 1's King Street
crossing is the truth audit's `crossing-1105419114-no-kerbs` case.

---

## 1. Uptown square to the transit hub — wheelchair

`crossing-uptown-to-hub`: (43.465, -80.5222) to (43.4664, -80.5228).

**PathAble says:** "+1 m to avoid 12 m of path with no recorded surface condition." Shortest 182.3 m, wheelchair 183.0 m. Each route crosses three signalised crossings recorded as lowered at both ends. Both cross King Street North on a marked crossing with no kerb recorded at either end.

### F1.1 · King Street North marked crossing

|                       |                                                                                                                                                 |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.466071, -80.522704 · [map](https://www.openstreetmap.org/?mlat=43.466071&mlon=-80.522704#map=19/43.466071/-80.522704)                        |
| **Near**              | about 25 m east of the Old Post Office, 35 King Street North                                                                                    |
| **OSM nodes**         | 10115733721, 10115733722, 6865304497                                                                                                            |
| **PathAble claims**   | No kerb recorded at either end: unknown.                                                                                                        |
| **Inspect**           | The kerb at each end of the crossing.                                                                                                           |
| **Bring back**        | Height in cm at each end (tape held vertical at the kerb's lip) and its type on the OSM Key:kerb scale; one photo of each end from the footway. |
| **Safety and access** | Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane.                                           |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F1.1                                                                    |

### F1.2 · Signalised crossing on the wheelchair route (first)

|                       |                                                                                                                                                 |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.465100, -80.522221 · [map](https://www.openstreetmap.org/?mlat=43.465100&mlon=-80.522221#map=19/43.465100/-80.522221)                        |
| **Near**              | about 35 m south-west of The Waterloo Hotel, 4 King Street North; Hughes Lane nearby                                                            |
| **OSM nodes**         | 8060737277, 8481245380                                                                                                                          |
| **PathAble claims**   | Lowered at both ends.                                                                                                                           |
| **Inspect**           | The kerb at each end.                                                                                                                           |
| **Bring back**        | Height in cm at each end (tape held vertical at the kerb's lip) and its type on the OSM Key:kerb scale; one photo of each end from the footway. |
| **Safety and access** | Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane.                                           |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F1.2                                                                    |

### F1.3 · Signalised crossing on the wheelchair route (second)

|                       |                                                                                                                                                 |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.465228, -80.522250 · [map](https://www.openstreetmap.org/?mlat=43.465228&mlon=-80.522250#map=19/43.465228/-80.522250)                        |
| **Near**              | about 25 m south-west of The Waterloo Hotel, 4 King Street North                                                                                |
| **OSM nodes**         | 6091347592, 6872489585, 8481245382, 8481245383                                                                                                  |
| **PathAble claims**   | Lowered at both ends.                                                                                                                           |
| **Inspect**           | The kerb at each end.                                                                                                                           |
| **Bring back**        | Height in cm at each end (tape held vertical at the kerb's lip) and its type on the OSM Key:kerb scale; one photo of each end from the footway. |
| **Safety and access** | Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane.                                           |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F1.3                                                                    |

### F1.4 · Signalised crossing on the wheelchair route (third)

|                       |                                                                                                                                                 |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.465304, -80.522401 · [map](https://www.openstreetmap.org/?mlat=43.465304&mlon=-80.522401#map=19/43.465304/-80.522401)                        |
| **Near**              | about 30 m west of The Waterloo Hotel, 4 King Street North; Dominion Lane nearby                                                                |
| **OSM nodes**         | 6872487579, 8060737272–8060737274                                                                                                               |
| **PathAble claims**   | Lowered at both ends.                                                                                                                           |
| **Inspect**           | The kerb at each end.                                                                                                                           |
| **Bring back**        | Height in cm at each end (tape held vertical at the kerb's lip) and its type on the OSM Key:kerb scale; one photo of each end from the footway. |
| **Safety and access** | Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane.                                           |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F1.4                                                                    |

### F1.5 · Signalised crossing on the shortest route (first)

|                       |                                                                                                                                                 |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.465075, -80.522399 · [map](https://www.openstreetmap.org/?mlat=43.465075&mlon=-80.522399#map=19/43.465075/-80.522399)                        |
| **Near**              | about 45 m south-west of The Waterloo Hotel, 4 King Street North                                                                                |
| **PathAble claims**   | Lowered at both ends.                                                                                                                           |
| **Inspect**           | The kerb at each end.                                                                                                                           |
| **Bring back**        | Height in cm at each end (tape held vertical at the kerb's lip) and its type on the OSM Key:kerb scale; one photo of each end from the footway. |
| **Safety and access** | Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane.                                           |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F1.5                                                                    |

### F1.6 · Signalised crossing on the shortest route (second)

|                       |                                                                                                                                                 |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.465202, -80.522501 · [map](https://www.openstreetmap.org/?mlat=43.465202&mlon=-80.522501#map=19/43.465202/-80.522501)                        |
| **Near**              | about 40 m south-west of The Waterloo Hotel, 4 King Street North; Dominion Lane nearby                                                          |
| **PathAble claims**   | Lowered at both ends.                                                                                                                           |
| **Inspect**           | The kerb at each end.                                                                                                                           |
| **Bring back**        | Height in cm at each end (tape held vertical at the kerb's lip) and its type on the OSM Key:kerb scale; one photo of each end from the footway. |
| **Safety and access** | Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane.                                           |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F1.6                                                                    |

### F1.7 · Signalised crossing on the shortest route (third)

|                       |                                                                                                                                                 |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.465288, -80.522496 · [map](https://www.openstreetmap.org/?mlat=43.465288&mlon=-80.522496#map=19/43.465288/-80.522496)                        |
| **Near**              | about 40 m west of The Waterloo Hotel, 4 King Street North; Dominion Lane nearby                                                                |
| **PathAble claims**   | Lowered at both ends.                                                                                                                           |
| **Inspect**           | The kerb at each end.                                                                                                                           |
| **Bring back**        | Height in cm at each end (tape held vertical at the kerb's lip) and its type on the OSM Key:kerb scale; one photo of each end from the footway. |
| **Safety and access** | Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane.                                           |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F1.7                                                                    |

### F1.8 · Both routes, end to end

|                       |                                                                                                                                                                                                      |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | The whole route (see the journey's ends above)                                                                                                                                                       |
| **Near**              | start about 30 m north-west of Central Block; Hughes Lane nearby; end about 15 m east of Kraft Block, 43,45 King Street North                                                                        |
| **PathAble claims**   | Nothing beyond the crossings above is recorded as a barrier on either route.                                                                                                                         |
| **Inspect**           | Any step, kerb, gate or surface problem on either route that the map does not show.                                                                                                                  |
| **Bring back**        | Every step, threshold, kerb, gate or surface problem met on the way, each with its approximate position (a dropped pin or coordinates) and a photo. Write 'none' if there were none.                 |
| **Safety and access** | Walk or roll at your own pace; stop and record rather than attempt anything that looks unsafe. Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F1.8                                                                                                                         |

---

## 2. Victoria Park to Kitchener Market — crutches

`long-victoria-park-to-market`: (43.446418, -80.498274) to (43.446358, -80.483738).

**PathAble says:** "+20 m for gentler gradients." Shortest 1,512.6 m, crutches 1,533.0 m. The origin snaps 16.0 m from the requested point.

### F2.1 · Unmarked crossing, on both routes

|                       |                                                                                                                                                 |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.445694, -80.495596 · [map](https://www.openstreetmap.org/?mlat=43.445694&mlon=-80.495596#map=19/43.445694/-80.495596)                        |
| **Near**              | about 40 m south-west of Crow Shield Lodge, 101 David Street                                                                                    |
| **OSM nodes**         | 1463004904, 8694896718                                                                                                                          |
| **PathAble claims**   | No kerb recorded at either end: unknown.                                                                                                        |
| **Inspect**           | The kerb at each end.                                                                                                                           |
| **Bring back**        | Height in cm at each end (tape held vertical at the kerb's lip) and its type on the OSM Key:kerb scale; one photo of each end from the footway. |
| **Safety and access** | Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane.                                           |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F2.1                                                                    |

### F2.2 · Joseph Street marked crossing, shortest route only

|                       |                                                                                                                                                 |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.448497, -80.491816 · [map](https://www.openstreetmap.org/?mlat=43.448497&mlon=-80.491816#map=19/43.448497/-80.491816)                        |
| **Near**              | about 50 m south-west of Jameison Pool Table and Dart Supplies, 103 Ontario Street South                                                        |
| **OSM nodes**         | 10133893874, 9191317994, 9194882938                                                                                                             |
| **PathAble claims**   | No kerb recorded at either end: unknown.                                                                                                        |
| **Inspect**           | The kerb at each end.                                                                                                                           |
| **Bring back**        | Height in cm at each end (tape held vertical at the kerb's lip) and its type on the OSM Key:kerb scale; one photo of each end from the footway. |
| **Safety and access** | Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane.                                           |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F2.2                                                                    |

### F2.3 · Steepest climb on the shortest route

|                     |                                                                                                                                                  |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Where**           | 43.448369, -80.488647 · [map](https://www.openstreetmap.org/?mlat=43.448369&mlon=-80.488647#map=19/43.448369/-80.488647)                         |
| **Near**            | about 60 m west of Oktoberfest Platz                                                                                                             |
| **OSM nodes**       | 7837713061, 8295118559                                                                                                                           |
| **PathAble claims** | 5.76% estimated over 30.1 m, concrete; no incline recorded.                                                                                      |
| **Inspect**         | The slope in the direction of travel.                                                                                                            |
| **Bring back**      | Three inclinometer readings in % laid along the direction of travel, the surface, and one photo along the slope showing the reading if possible. |
| **Result**          | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F2.3                                                                     |

### F2.4 · Steepest climb on the crutches route

|                     |                                                                                                                                                  |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Where**           | 43.448456, -80.488578 · [map](https://www.openstreetmap.org/?mlat=43.448456&mlon=-80.488578#map=19/43.448456/-80.488578)                         |
| **Near**            | about 55 m west of Oktoberfest Platz                                                                                                             |
| **OSM nodes**       | 1475099754, 7837713053                                                                                                                           |
| **PathAble claims** | 5.3% estimated over 25.1 m, asphalt. The route exists because this climb is gentler than F2.3.                                                   |
| **Inspect**         | The slope in the direction of travel, and whether it is in fact gentler than F2.3.                                                               |
| **Bring back**      | Three inclinometer readings in % laid along the direction of travel, the surface, and one photo along the slope showing the reading if possible. |
| **Result**          | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F2.4                                                                     |

---

## 3. Verified example: Davis Centre library to the Student Life Centre — wheelchair

`landing page record`: (43.4728, -80.5424) to (43.4715, -80.5449).

**PathAble says:** "+67 m to avoid 3 recorded stairways." Shortest 287.4 m with three stairways, wheelchair 354.1 m with none. Both end 15.3 m from the requested destination, at 43.471367, -80.544851.

### F3.1 · Stairway with 5 recorded steps

|                       |                                                                                                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.472359, -80.542835 · [map](https://www.openstreetmap.org/?mlat=43.472359&mlon=-80.542835#map=19/43.472359/-80.542835)                                                                 |
| **Near**              | about 40 m north of Chemistry 2; Path 2 East nearby                                                                                                                                      |
| **OSM nodes**         | 1505832303, 1505832399                                                                                                                                                                   |
| **PathAble claims**   | A stairway of 5 steps.                                                                                                                                                                   |
| **Inspect**           | The step count; any handrail or ramp beside it.                                                                                                                                          |
| **Bring back**        | Step count, number of flights, handrail (none / one side / both sides), any ramp or lift beside it; one photo from the bottom, one from the top.                                         |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Count from the side of the stairway and keep the way clear for others. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F3.1                                                                                                             |

### F3.2 · Stairway, step count not recorded

|                       |                                                                                                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.471954, -80.543423 · [map](https://www.openstreetmap.org/?mlat=43.471954&mlon=-80.543423#map=19/43.471954/-80.543423)                                                                 |
| **Near**              | about 40 m west of Chemistry 2; Path 2 East nearby                                                                                                                                       |
| **OSM nodes**         | 1951555104, 1951555165, 1951555169                                                                                                                                                       |
| **PathAble claims**   | A stairway; how many steps is unknown.                                                                                                                                                   |
| **Inspect**           | The step count.                                                                                                                                                                          |
| **Bring back**        | Step count, number of flights, handrail (none / one side / both sides), any ramp or lift beside it; one photo from the bottom, one from the top.                                         |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Count from the side of the stairway and keep the way clear for others. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F3.2                                                                                                             |

### F3.3 · Stairway with 11 recorded steps

|                       |                                                                                                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.471690, -80.544077 · [map](https://www.openstreetmap.org/?mlat=43.471690&mlon=-80.544077#map=19/43.471690/-80.544077)                                                                 |
| **Near**              | about 50 m south of Mathematics & Computer; MC to QNC Walkway nearby                                                                                                                     |
| **OSM nodes**         | 1932767867, 1976914988                                                                                                                                                                   |
| **PathAble claims**   | A stairway of 11 steps.                                                                                                                                                                  |
| **Inspect**           | The step count.                                                                                                                                                                          |
| **Bring back**        | Step count, number of flights, handrail (none / one side / both sides), any ramp or lift beside it; one photo from the bottom, one from the top.                                         |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Count from the side of the stairway and keep the way clear for others. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F3.3                                                                                                             |

### F3.4 · The wheelchair route, end to end

|                       |                                                                                                                                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | The whole route (see the journey's ends above)                                                                                                                                                                   |
| **Near**              | start about 45 m north-west of William G. Davis Computer Research Centre; end about 60 m south-east of Student Life Centre; Path 2 West nearby                                                                   |
| **PathAble claims**   | Step-free end to end: no stairway recorded on it. This is the claim the landing page makes, and the one most worth contradicting if it is wrong.                                                                 |
| **Inspect**           | Any step, threshold, door or kerb on it.                                                                                                                                                                         |
| **Bring back**        | Every step, threshold, kerb, gate or surface problem met on the way, each with its approximate position (a dropped pin or coordinates) and a photo. Write 'none' if there were none.                             |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Walk or roll at your own pace; stop and record rather than attempt anything that looks unsafe. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F3.4                                                                                                                                     |

### F3.5 · Entrance approach at the Student Life Centre

|                       |                                                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.471367, -80.544851 · [map](https://www.openstreetmap.org/?mlat=43.471367&mlon=-80.544851#map=19/43.471367/-80.544851)                 |
| **Near**              | about 55 m west of Mike & Ophelia Lazaridis Quantum Nano Centre; Path 2 West nearby                                                      |
| **PathAble claims**   | Nothing: both routes end on the path network 15.3 m from the requested point; PathAble knows no doors.                                   |
| **Inspect**           | From the routes' end, the nearest Student Life Centre door: step-free or not, and how far.                                               |
| **Bring back**        | The door reached, the distance to it (paces or a measuring app), whether the way is step-free, and a photo of the approach and the door. |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours.                        |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F3.5                                                             |

---

## 4. Searched journey: Davis Centre Library to the Student Life Centre — wheelchair

`campus-dc-to-slc`: (43.472428, -80.541997) to (43.471972, -80.545213).

**PathAble says:** "+102 m to avoid 3 recorded stairways." Shortest 401.3 m over the same three stairways as journey 3; wheelchair 503.8 m with none (503.7 m from the unrounded searched ends, which the live smoke test checks).

### F4.1 · The wheelchair route, end to end

|                       |                                                                                                                                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | The whole route (see the journey's ends above)                                                                                                                                                                   |
| **Near**              | start at Davis Centre Library; end at Student Life Centre                                                                                                                                                        |
| **PathAble claims**   | Step-free end to end. It differs from journey 3's route.                                                                                                                                                         |
| **Inspect**           | Any step, threshold, door or kerb on it.                                                                                                                                                                         |
| **Bring back**        | Every step, threshold, kerb, gate or surface problem met on the way, each with its approximate position (a dropped pin or coordinates) and a photo. Write 'none' if there were none.                             |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Walk or roll at your own pace; stop and record rather than attempt anything that looks unsafe. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F4.1                                                                                                                                     |

### F4.2 · Unmarked crossing recorded as lowered

|                       |                                                                                                                                                                                                                         |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.473561, -80.542046 · [map](https://www.openstreetmap.org/?mlat=43.473561&mlon=-80.542046#map=19/43.473561/-80.542046)                                                                                                |
| **Near**              | about 45 m east of General Services Complex                                                                                                                                                                             |
| **OSM nodes**         | 1450232815, 8307684710, 8307684714                                                                                                                                                                                      |
| **PathAble claims**   | Lowered at both ends.                                                                                                                                                                                                   |
| **Inspect**           | The kerb at each end.                                                                                                                                                                                                   |
| **Bring back**        | Height in cm at each end (tape held vertical at the kerb's lip) and its type on the OSM Key:kerb scale; one photo of each end from the footway.                                                                         |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F4.2                                                                                                                                            |

### F4.3 · Steepest climb on the wheelchair route

|                       |                                                                                                                                                  |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Where**             | 43.473066, -80.541622 · [map](https://www.openstreetmap.org/?mlat=43.473066&mlon=-80.541622#map=19/43.473066/-80.541622)                         |
| **Near**              | about 30 m south-west of Univ of Waterloo Interlocking                                                                                           |
| **OSM nodes**         | 1976894147, 1976894119                                                                                                                           |
| **PathAble claims**   | 4.15% estimated over 16.2 m, paving stones.                                                                                                      |
| **Inspect**           | The slope in the direction of travel.                                                                                                            |
| **Bring back**        | Three inclinometer readings in % laid along the direction of travel, the surface, and one photo along the slope showing the reading if possible. |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours.                                |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F4.3                                                                     |

### F4.4 · Entrance approach at the Student Life Centre

|                       |                                                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.471972, -80.545213 · [map](https://www.openstreetmap.org/?mlat=43.471972&mlon=-80.545213#map=19/43.471972/-80.545213)                 |
| **Near**              | at Student Life Centre                                                                                                                   |
| **PathAble claims**   | Nothing: both routes end 1.2 m from the requested point; PathAble knows no doors.                                                        |
| **Inspect**           | The step-free way, if any, from there to the nearest Student Life Centre door.                                                           |
| **Bring back**        | The door reached, the distance to it (paces or a measuring app), whether the way is step-free, and a photo of the approach and the door. |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours.                        |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F4.4                                                             |

---

## 5. Physical Activities Complex to Dana Porter Library — walker

`campus-pac-to-dp`: (43.472406, -80.546114) to (43.469733, -80.542271).

**PathAble says:** "Ends 52 m away: the map has no path for this profile to where the shortest route ends." Shortest 585.3 m with two stairways, ending at 43.469638, -80.542545. Walker route 567.7 m, ending at 43.469930, -80.542040, 28.8 m from the requested point. The two lengths are not compared.

### F5.1 · Stairway near Dana Porter, step count not recorded

|                       |                                                                                                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.469611, -80.542623 · [map](https://www.openstreetmap.org/?mlat=43.469611&mlon=-80.542623#map=19/43.469611/-80.542623)                                                                 |
| **Near**              | about 30 m south-west of Dana Porter Library                                                                                                                                             |
| **OSM nodes**         | 1999591897, 1999591925                                                                                                                                                                   |
| **PathAble claims**   | A stairway on the shortest route's way to its end, with no step-free path past it on the map; that is why the walker route ends elsewhere.                                               |
| **Inspect**           | The step count, and whether any step-free way bypasses it.                                                                                                                               |
| **Bring back**        | Step count, number of flights, handrail (none / one side / both sides), any ramp or lift beside it; one photo from the bottom, one from the top.                                         |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Count from the side of the stairway and keep the way clear for others. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F5.1                                                                                                             |

### F5.2 · Stairway, step count not recorded

|                       |                                                                                                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.471407, -80.544594 · [map](https://www.openstreetmap.org/?mlat=43.471407&mlon=-80.544594#map=19/43.471407/-80.544594)                                                                 |
| **Near**              | about 35 m north-west of Mike & Ophelia Lazaridis Quantum Nano Centre; Path 2 West nearby                                                                                                |
| **OSM nodes**         | 3725159562, 3725159563                                                                                                                                                                   |
| **PathAble claims**   | A stairway; how many steps is unknown.                                                                                                                                                   |
| **Inspect**           | The step count.                                                                                                                                                                          |
| **Bring back**        | Step count, number of flights, handrail (none / one side / both sides), any ramp or lift beside it; one photo from the bottom, one from the top.                                         |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Count from the side of the stairway and keep the way clear for others. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F5.2                                                                                                             |

### F5.3 · Uncontrolled crossing on the shortest route

|                       |                                                                                                                                                                                                                         |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.470319, -80.544262 · [map](https://www.openstreetmap.org/?mlat=43.470319&mlon=-80.544262#map=19/43.470319/-80.544262)                                                                                                |
| **Near**              | about 70 m south-west of Science Teaching Complex                                                                                                                                                                       |
| **OSM nodes**         | 5502132895–5502132897                                                                                                                                                                                                   |
| **PathAble claims**   | No kerb recorded at either end: unknown.                                                                                                                                                                                |
| **Inspect**           | The kerb at each end.                                                                                                                                                                                                   |
| **Bring back**        | Height in cm at each end (tape held vertical at the kerb's lip) and its type on the OSM Key:kerb scale; one photo of each end from the footway.                                                                         |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Street crossing: measure from the sidewalk or a signalled refuge; never stand in a live traffic lane. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F5.3                                                                                                                                            |

### F5.4 · Steepest climb on both routes

|                       |                                                                                                                                                  |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Where**             | 43.472874, -80.545686 · [map](https://www.openstreetmap.org/?mlat=43.472874&mlon=-80.545686#map=19/43.472874/-80.545686)                         |
| **Near**              | about 40 m west of B.C. Matthews Hall Green                                                                                                      |
| **OSM nodes**         | 1436010067, 1607240665                                                                                                                           |
| **PathAble claims**   | 8.33% estimated over 43.9 m; surface not recorded.                                                                                               |
| **Inspect**           | The slope in the direction of travel, and the surface.                                                                                           |
| **Bring back**        | Three inclinometer readings in % laid along the direction of travel, the surface, and one photo along the slope showing the reading if possible. |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours.                                |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F5.4                                                                     |

### F5.5 · Entrance approach at Dana Porter Library

|                       |                                                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.469930, -80.542040 · [map](https://www.openstreetmap.org/?mlat=43.469930&mlon=-80.542040#map=19/43.469930/-80.542040)                 |
| **Near**              | about 30 m north-east of Dana Porter Library; Two Row Path nearby                                                                        |
| **PathAble claims**   | Nothing: the walker route ends 28.8 m from the requested point; PathAble knows no doors (KI-16).                                         |
| **Inspect**           | From where the walker route ends, Dana Porter's nearest usable entrance: reachable with a walker without steps, and how.                 |
| **Bring back**        | The door reached, the distance to it (paces or a measuring app), whether the way is step-free, and a photo of the approach and the door. |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours.                        |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F5.5                                                             |

---

## 6. Steep path in south Kitchener, uphill — wheelchair

`gradient-40585154-up`: (43.438097, -80.531191) to (43.437598, -80.537267).

**PathAble says:** "+234 m for gentler gradients." Shortest 564.1 m, wheelchair 797.7 m. Neither route has a recorded incline; every gradient here is estimated from the terrain model.

### F6.1 · Steepest climb on the shortest route

|                       |                                                                                                                                                  |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Where**             | 43.437598, -80.536960 · [map](https://www.openstreetmap.org/?mlat=43.437598&mlon=-80.536960#map=19/43.437598/-80.536960)                         |
| **Near**              | no named place nearby in the place index; use the map link                                                                                       |
| **OSM nodes**         | 388003348, 388003349                                                                                                                             |
| **PathAble claims**   | 16.35% estimated over 8.5 m, fine gravel. A segment this short is where a terrain model is least reliable.                                       |
| **Inspect**           | The slope in the direction of travel.                                                                                                            |
| **Bring back**        | Three inclinometer readings in % laid along the direction of travel, the surface, and one photo along the slope showing the reading if possible. |
| **Safety and access** | Gravel path: go in daylight and dry weather; the climb is steep by PathAble's estimate.                                                          |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F6.1                                                                     |

### F6.2 · Steepest climb on the wheelchair route

|                       |                                                                                                                                                  |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Where**             | 43.436644, -80.534909 · [map](https://www.openstreetmap.org/?mlat=43.436644&mlon=-80.534909#map=19/43.436644/-80.534909)                         |
| **Near**              | no named place nearby in the place index; use the map link                                                                                       |
| **OSM nodes**         | 388003440, 388003441                                                                                                                             |
| **PathAble claims**   | 11.15% estimated over 14.1 m, fine gravel. If it is as steep as estimated, the 'gentler' route is still steep and should be described so.        |
| **Inspect**           | The slope in the direction of travel.                                                                                                            |
| **Bring back**        | Three inclinometer readings in % laid along the direction of travel, the surface, and one photo along the slope showing the reading if possible. |
| **Safety and access** | Gravel path: go in daylight and dry weather; the climb is steep by PathAble's estimate.                                                          |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F6.2                                                                     |

### F6.3 · Surface of both climbs

|                       |                                                                                                                                  |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | The whole route (see the journey's ends above)                                                                                   |
| **Near**              | start no named place nearby in the place index; use the map link; end no named place nearby in the place index; use the map link |
| **PathAble claims**   | Fine gravel on both.                                                                                                             |
| **Inspect**           | The surface at F6.1 and F6.2, and its condition.                                                                                 |
| **Bring back**        | Surface type and condition along the route (smooth, cracked, loose, puddled), with a photo of each change.                       |
| **Safety and access** | Gravel path: go in daylight and dry weather; the climb is steep by PathAble's estimate.                                          |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F6.3                                                     |

---

## 7. Modern Languages to South Campus Hall — wheelchair

`campus-ml-to-sch`: (43.468984, -80.542737) to (43.469212, -80.540208).

**PathAble says:** "Same path as shortest route." 272.2 m. No segment on the route has a recorded surface.

### F7.1 · Steepest climb, next to the start

|                       |                                                                                                                                                  |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Where**             | 43.469009, -80.542721 · [map](https://www.openstreetmap.org/?mlat=43.469009&mlon=-80.542721#map=19/43.469009/-80.542721)                         |
| **Near**              | at Modern Languages                                                                                                                              |
| **OSM nodes**         | 11304345811, 3872192573                                                                                                                          |
| **PathAble claims**   | 9.29% estimated over a 2.0 m segment. A 2 m climb this steep can be a ramp, a kerb or a step, and the map records none of them.                  |
| **Inspect**           | What is there, and its slope.                                                                                                                    |
| **Bring back**        | Three inclinometer readings in % laid along the direction of travel, the surface, and one photo along the slope showing the reading if possible. |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours.                                |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F7.1                                                                     |

### F7.2 · Surface along the route

|                       |                                                                                                                                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | The whole route (see the journey's ends above)                                                                                                                                                                   |
| **Near**              | start at Modern Languages; end at South Campus Hall; Main Path nearby                                                                                                                                            |
| **PathAble claims**   | No surface recorded anywhere on the route: unknown.                                                                                                                                                              |
| **Inspect**           | The surface along the whole route.                                                                                                                                                                               |
| **Bring back**        | Surface type and condition along the route (smooth, cracked, loose, puddled), with a photo of each change.                                                                                                       |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Walk or roll at your own pace; stop and record rather than attempt anything that looks unsafe. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F7.2                                                                                                                                     |

### F7.3 · A better way on the ground

|                       |                                                                                                                                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | The whole route (see the journey's ends above)                                                                                                                                                                   |
| **Near**              | start at Modern Languages; end at South Campus Hall; Main Path nearby                                                                                                                                            |
| **PathAble claims**   | 'Same path' means the wheelchair profile found no better way on the map.                                                                                                                                         |
| **Inspect**           | Whether a better way for a wheelchair exists on the ground.                                                                                                                                                      |
| **Bring back**        | Every step, threshold, kerb, gate or surface problem met on the way, each with its approximate position (a dropped pin or coordinates) and a photo. Write 'none' if there were none.                             |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Walk or roll at your own pace; stop and record rather than attempt anything that looks unsafe. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F7.3                                                                                                                                     |

---

## 8. Campus stairway, 71 m, bottom to top — wheelchair

`stairs-700726011`: (43.471175, -80.549346) to (43.471515, -80.549858).

**PathAble says:** "Ends 11 m away: the map has no path for this profile to where the shortest route ends." Shortest 71.4 m up the stairway. The step-free route is 353.2 m and ends 11.2 m from the top.

### F8.1 · The stairway

|                       |                                                                                                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.471318, -80.549721 · [map](https://www.openstreetmap.org/?mlat=43.471318&mlon=-80.549721#map=19/43.471318/-80.549721)                                                                 |
| **Near**              | about 25 m north-east of South 1                                                                                                                                                         |
| **OSM nodes**         | 1436010165, 6580409239–6580409244, 7643481650                                                                                                                                            |
| **PathAble claims**   | A stairway 71.3 m long; step count not recorded.                                                                                                                                         |
| **Inspect**           | Steps and flights; handrails and landings.                                                                                                                                               |
| **Bring back**        | Step count, number of flights, handrail (none / one side / both sides), any ramp or lift beside it; one photo from the bottom, one from the top.                                         |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Count from the side of the stairway and keep the way clear for others. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F8.1                                                                                                             |

### F8.2 · The step-free route, and its steepest climb

|                       |                                                                                                                                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.472079, -80.548427 · [map](https://www.openstreetmap.org/?mlat=43.472079&mlon=-80.548427#map=19/43.472079/-80.548427)                                                                                         |
| **Near**              | about 30 m north-east of East 3                                                                                                                                                                                  |
| **OSM nodes**         | 1436010059, 1436010150                                                                                                                                                                                           |
| **PathAble claims**   | Step-free; steepest climb 4.73% estimated over 12.4 m, asphalt.                                                                                                                                                  |
| **Inspect**           | Any step, kerb or surface problem on the route, and the slope of its steepest climb.                                                                                                                             |
| **Bring back**        | Three inclinometer readings in % laid along the direction of travel, the surface, and one photo along the slope showing the reading if possible.                                                                 |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours. Walk or roll at your own pace; stop and record rather than attempt anything that looks unsafe. |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F8.2                                                                                                                                     |

### F8.3 · Where the step-free route ends

|                       |                                                                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **Where**             | 43.471515, -80.549858 · [map](https://www.openstreetmap.org/?mlat=43.471515&mlon=-80.549858#map=19/43.471515/-80.549858)                 |
| **Near**              | about 15 m south-east of Student Village 1 Central Complex                                                                               |
| **PathAble claims**   | The map has no step-free path to the top of the stairway; the route ends 11.2 m from it. The point is the requested top.                 |
| **Inspect**           | Whether the top of the stairway can be reached step-free from where the route ends.                                                      |
| **Bring back**        | The door reached, the distance to it (paces or a measuring app), whether the way is step-free, and a photo of the approach and the door. |
| **Safety and access** | University of Waterloo grounds: stay on paths open to the public; buildings and doors may be locked out of hours.                        |
| **Result**            | ☐ match · ☐ mismatch · ☐ unable to determine — record in the sheet, row F8.3                                                             |
