# ADR 0008 — The accessibility cost model

- **Status**: Accepted
- **Date**: 2026-08-12
- **Supersedes**: nothing
- **Related**: [0004 PostGIS and the runtime graph](0004-postgis-and-runtime-graph.md),
  [0006 configurable Waterloo pilot](0006-configurable-waterloo-pilot.md)

## Context

PathAble has to turn "how does this person travel?" into "which way should they
go?". The mechanism decides what the product is, so it is worth stating the
decision rather than letting it accumulate.

Three properties had to hold:

1. A route must be **explainable**. Somebody deciding whether they can make a
   journey needs to know what the alternative cost them and why, in terms they
   can disagree with.
2. Missing data must never read as good data. OpenStreetMap's accessibility
   coverage is sparse and uneven, and the least-surveyed places are not the most
   accessible ones.
3. Nothing may look like a prediction. There is no model here, and the design
   must not leave room to imply there is.

## Decision

### Costs are _effective metres_

`evaluate_edge` returns what a segment costs in metres-of-effort, not an
abstract score. A 100 m gravel path costing 250 effective metres means "this
feels like two and a half times the distance".

The unit is the point. A dimensionless score of 0.37 cannot be argued with; a
claim that a rough 100 m path is worth 250 m of clear pavement can be, and being
arguable is what makes it honest. Every contribution is returned alongside the
total with the recorded attribute that produced it, so a route can show its
working down to the segment.

### Hard constraints and penalties are separate, and hard constraints fire only on evidence

A **hard constraint** removes a segment from the graph entirely. It is used only
where travel is genuinely not possible: a stairway in a wheelchair, a recorded
width narrower than the chair. A hard constraint can make a journey unroutable,
and that is sometimes the correct answer.

A **penalty** makes a segment cost more. Everything that is harder rather than
impossible lives here: rough surface, a steep but passable grade, an unrecorded
kerb, a crossing.

Critically, **no hard constraint fires on the absence of a tag**. A missing
width, gradient or surface never excludes a segment. The alternative would delete
most of the network — and it would delete precisely the parts nobody has
surveyed, which is the opposite of what a user needs.

### Absence is charged as uncertainty

Missing routing-relevant attributes add cost proportional to segment length and
to how many attributes are missing. Without this, the cheapest path through the
network is the one carrying the least information, which exactly inverts what
somebody needs from the product.

The penalty scales with length because a 500 m unsurveyed path is a bigger gamble
than a 20 m one. Every route also reports what is missing, and the UI says
plainly that missing data is not evidence a path is clear.

**Measured on real data (2026-08-17).** The concern with this penalty was that it
was calibrated on a synthetic fixture where missing data was the exception, and
would saturate on a real extract where it is the norm — applying roughly
uniformly, cancelling out of every comparison, and inflating every effective
distance while distinguishing nothing.

Ablated over the twenty-journey Waterloo corpus on the 180,554-segment dataset:

| Variant                            | Routes changed, policy 2 | policy 3 | Median cost / real distance, policy 2 | policy 3 |
| ---------------------------------- | ------------------------ | -------- | ------------------------------------- | -------- |
| Penalties as shipped               | —                        | —        | 1.55×                                 | 1.51×    |
| No penalty for a missing gradient  | 3 / 20                   | 7 / 20   | 1.51×                                 | 1.46×    |
| No penalty for missing data at all | 3 / 20                   | 8 / 20   | 1.51×                                 | 1.46×    |

The penalty is **not** saturated: it changes the route on 8 of 20 real journeys
under policy 3 (3 under policy 2). Removing only the gradient term changes 7 of
them, so on this data the gradient penalty still does most of the work; one
journey (`laurel-creek-to-campus`) moves only when the other unknown terms go
too. Under policy 2 the gradient term alone had exactly the same effect as
removing every term. The change has not been attributed journey by journey. That is worth knowing before tuning any
of them, and it is why elevation mattered: gradient was recorded on 46 of
180,554 segments before HRDEM sampling and is derivable on 97,131 after.

The per-route figure reported to users changed as a result of this work. One
combined "missing data" percentage was replaced by per-category shares, because
a route missing every surface tag and one missing every gradient produce the same
number and are completely different journeys.

### The standard route is the same engine with a zero-penalty profile

There is one Dijkstra implementation. The "standard" route is the identical code
run with a profile whose penalties are all zero. Two implementations would
eventually diverge for reasons that had nothing to do with the user's
preferences, and the entire product is the comparison between them.

The standard profile still refuses segments where foot access is prohibited:
that is a legal fact about the way, not a preference.

### The two errors are not symmetric

Telling somebody a blocked path is passable can strand them somewhere they
cannot leave. Telling them a passable path is difficult costs them a detour. The
weights lean toward the second, deliberately.

### Why the routes differ is read from the same costs _(PA-UX-03A, 2026-09-25)_

Explanations used to be written from a scan of the segments the shortest route
used and the accessible one did not. That produced statements for stairs, some
kerbs, rough surface and recorded incline, and nothing for the other constraints
the profile weighs — so a detour caused by crossings and missing data read as
though stairs were the whole story.

Both routes are now costed under the chosen profile with `evaluate_edge`, and a
statement is made for every constraint on which they differ: each hard limit the
shortest route breaks, then each penalty the chosen route incurs less of, largest
difference first. A penalty charged per kind or per class (kerb, crossing,
surface, surface condition) is stated per kind, so a count in the sentence always
moves in the same direction as the cost behind it. A constraint the profile does
not charge is never claimed.

Every statement also carries its `basis` — recorded in OpenStreetMap, estimated
from the elevation model, both, not recorded, or the profile's own rule — so an
absence of data is never labelled as an observation.

### One crossing is one crossing _(routing policy 3, Waterloo Core v2, 2026-11-02)_

The network keeps every vertex of an OpenStreetMap way, so a crossing drawn from
kerb to kerb through the road's centreline arrives as two or more segments
(5,430 of Waterloo's 5,682 crossing ways), and so do 41 of 374 stairways. Flat
penalties were charged per piece. The middle of a crossing touched no kerb node
and read as "no kerb recorded", which applied to 424 crossings recorded as
dropped at both ends. Counts shown to people counted pieces. On a real journey
(uptown Waterloo to the transit hub) the wheelchair route detoured 97 m around a
signalised crossing recorded as dropped at both ends, because it was charged two
phantom unrecorded-kerb penalties.

Each crossing or stairway way is now one unit (`routing/units.py`). Its flat
penalties are spread over its pieces in proportion to length, so walking the
whole unit pays each once, and every segment still costs at least its length,
which keeps A\* admissible. A crossing's kerb is read from every kerb recorded
along it:

- two or more recorded kerbs give the worst of them;
- one recorded kerb leaves the crossing unknown, because a kerb nobody recorded
  is not a dropped one;
- none is unknown.

Policy 2 read one recorded kerb two ways, depending on how many segments the
crossing happened to be drawn in. Counts are of units. Evidence and the
before/after on twenty real journeys are in
[`WATERLOO_CORE_V2.md`](../evidence/WATERLOO_CORE_V2.md).

### The shortest route is the baseline, timed at walking pace _(2026-11-02)_

The comparison is "shortest pedestrian route" against "your profile's route".
The shortest route was always computed for standard walking, but it was
re-timed at the chosen profile's pace, so switching profile changed the
baseline's time. It is now timed at walking pace whatever profile it is shown
beside. Time is distance over an assumed pace range (walking 1.1–1.4 m/s; each
profile its own), with no fixed allowance for crossings or steps. The two
routes' times are at different paces and are never subtracted. Time does not
take part in route selection.

### Both ends snap to paths that connect _(2026-11-02)_

Connected parts are numbered when the graph loads, over walkable segments and,
separately, step-free ones. When the paths nearest the two ends never meet, one
end moves to the nearest connecting path within the same 150 m, preferring the
larger part, and the comparison says so in a caution. A moved end must still be
nearer the point it stands for than the other end is: moved to the foot of a
stairway somebody asked to climb, the nearest step-free path was the start
itself, a route of 0 m. When an end has moved, the summary leads with where the
profile route ends, not with how much shorter it is, because two routes that end
in different places are not one journey made two ways. Otherwise the request
fails at once with that reason. This replaces a "no route" for a building whose
nearest path is behind a private door or reachable only by stairs, and a search
of the whole network for two points that cannot be joined.

## Consequences

- A route can always be explained from the same numbers the router optimised.
  An explanation reconstructed separately would eventually stop matching.
- A profile can legitimately have no route. The API returns the shortest route
  anyway, along with the specific segments that blocked the accessible one, so
  "no route" arrives with a reason.
- The weights are engineering judgement, **not measurements**. They have not been
  validated against how people using these mobility aids actually travel. The
  product must not present them as if they had been, and validating them with
  real users is a prerequisite for taking this beyond a pilot.
- When learned predictions eventually exist, they enter as their own cost
  contribution with their own code, sitting beside the deterministic ones rather
  than replacing them — so "OSM says" and "a model thinks" stay distinguishable
  in the response, and `ml_predictions_used` becomes a meaningful flag rather
  than a constant.

## Alternatives considered

**A single accessibility score per segment.** Simpler to render and impossible to
argue with. It would have collapsed "this is rough", "this is steep" and "nobody
has looked" into one number, and it is exactly the kind of invented metric this
project must not ship.

**Excluding every segment with unknown attributes.** Safe-sounding and unusable:
it would refuse to route across most of a real city, and it would refuse hardest
in the least-mapped neighbourhoods.

**Treating unknown as acceptable.** The cheapest route would then be the least
documented one, and the product would confidently recommend paths nobody has
checked.
