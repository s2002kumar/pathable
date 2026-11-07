# Test data

## `uw-campus.osm.pbf` — OpenStreetMap, University of Waterloo campus

**© OpenStreetMap contributors. Available under the
[Open Database Licence 1.0](https://opendatacommons.org/licenses/odbl/1-0/)**
([openstreetmap.org/copyright](https://www.openstreetmap.org/copyright)). This
file is an extract of the OpenStreetMap database, a Derivative Database under
ODbL, and is offered under ODbL 1.0. It is not covered by the repository's
all-rights-reserved terms for code ([LICENSING.md](../../../../LICENSING.md) §2).

|               |                                                                                                                                      |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| What          | Every node in the box, every way touching it (whole), and the two building multipolygons among its relations                         |
| Box           | south 43.4690, west −80.5490, north 43.4755, east −80.5385 (about 850 m × 720 m around the Davis Centre and the Student Life Centre) |
| Contents      | 10,391 nodes, 1,734 ways (584 footways, 76 stairways), 2 relations                                                                   |
| Data as of    | 2026-11-07T02:48:06Z, the `osm_base` Overpass reported; also in the file's header                                                    |
| Retrieved     | 2026-11-07 from `https://z.overpass-api.de/api/interpreter`, one request                                                             |
| Source sha256 | `5a5434e3e96404c75816913c7ed0a3e2536a528a61701bebf86a31e4118e8f0b` (the 9.5 MB XML response, not committed)                          |
| File sha256   | `6b0060a7bc37e831b92a579695d5d46ce8330bf169b730cf8beedb795ff249f9`, reproduced by a second build                                     |
| Elevation     | None. Gradients on it are recorded (`incline` tags) or unknown, never derived                                                        |
| Personal data | Contributor names, user ids and changeset ids removed; versions and timestamps kept                                                  |

The query:

```
[out:xml][timeout:150];
(nwr(43.4690,-80.5490,43.4755,-80.5385););
(._;>;);
out meta;
```

[`build_campus_fixture.py`](build_campus_fixture.py) turns the response into this
file. It drops route, boundary and transit relations, which reach across
southern Ontario and which nothing on campus needs; it keeps building
multipolygons because the Davis Centre is one.

### What it is for, and what it is not

It is what the full-stack CI job routes and searches on, so the place-search and
demo browser suites run there on real OpenStreetMap tagging and topology instead
of skipping. CI loads it through the same lifecycle an operator uses — ingest,
seal, evaluate, accept with a recorded reason, activate — and builds the place
index from it (`.github/workflows/ci.yml`, job `fullstack-e2e`).

It is **not** the pilot's network. That is the whole Waterloo region from a
Geofabrik extract of 2026-08-16, with the HRDEM elevation model; this is one
campus, three months later, with none. Routes on it are real routes over real
data and are not the pilot's figures. Never load it into a deployment.

### Refreshing it

Run the query against an Overpass endpoint, then, from `services/api`:

```sh
uv run python -m tests.data.build_campus_fixture response.osm tests/data/uw-campus.osm.pbf <osm_base>
```

and update the `--source-timestamp` in `ci.yml` and the table above.
