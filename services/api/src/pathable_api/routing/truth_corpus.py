"""The journeys the product truth audit routes (`routing.truth_audit`).

Fixed before any of them was run under the audit, and versioned: a change to the
list is a new corpus version, so two audits can say whether they routed the same
journeys. **No case is here because of what it produced.** Each is here because
of what it is — a campus walk, a stairway, a steep path, a crossing with no kerb
record — and every one is reported whatever happened.

Two kinds of position:

* **Named places** are the points the place index holds for them, read from the
  same OpenStreetMap extract as the network (dataset `51585450…`, data as of
  2026-08-16). A route starts where a person searching for the place would.
* **Feature cases** start and end at the two end nodes of one OpenStreetMap way,
  chosen from the dataset by a stated rule: the longest stairways, the longest
  steep footpaths, `foot=no` ways and uptown crossings with 0, 2 and 4 kerb
  records. The way id is in the key, so the feature can be found again.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bumped whenever a case is added, removed or moved.
TRUTH_CORPUS_VERSION = 1


@dataclass(frozen=True, slots=True)
class AuditCase:
    key: str
    description: str
    #: What the case is for: campus, long, stairs, gradient, crossings,
    #: missing_surface, foot_no, snapping, identical, large_detour.
    categories: tuple[str, ...]
    origin: tuple[float, float]
    destination: tuple[float, float]


# --- Named places (longitude, latitude), from the place index -------------------
DC_LIBRARY = (-80.541997, 43.472428)
DC_BUILDING = (-80.542041, 43.472502)
STUDENT_LIFE_CENTRE = (-80.545213, 43.471972)
MATHEMATICS_AND_COMPUTER = (-80.543927, 43.472107)
ENGINEERING_5 = (-80.540083, 43.472922)
PHYSICAL_ACTIVITIES_COMPLEX = (-80.546114, 43.472406)
DANA_PORTER_LIBRARY = (-80.542271, 43.469733)
ENVIRONMENT_3 = (-80.543251, 43.467993)
ENGINEERING_3 = (-80.540719, 43.471675)
RON_EYDT_VILLAGE = (-80.552981, 43.469552)
MODERN_LANGUAGES = (-80.542737, 43.468984)
SOUTH_CAMPUS_HALL = (-80.540208, 43.469212)
ST_JEROMES = (-80.546234, 43.469428)
RENISON = (-80.547771, 43.468927)
COLUMBIA_ICEFIELD = (-80.548455, 43.475448)
FEDERATION_HALL = (-80.548604, 43.473212)
CONRAD_GREBEL = (-80.545385, 43.467074)
UW_STATION = (-80.541278, 43.473362)
PHYSICS = (-80.541544, 43.470848)
CHEMISTRY_2 = (-80.542953, 43.472025)
BIOLOGY_1 = (-80.542960, 43.470717)
CARL_POLLOCK_HALL = (-80.539282, 43.470966)
FRED_NICHOLS_CENTRE = (-80.528841, 43.473456)
WATERLOO_PUBLIC_SQUARE = (-80.522803, 43.464186)
WATERLOO_PARK = (-80.532477, 43.466896)
GRAND_RIVER_HOSPITAL = (-80.511853, 43.457228)
KITCHENER_CITY_HALL = (-80.491076, 43.452026)
BELMONT_VILLAGE = (-80.517957, 43.453340)
ALLEN_STATION = (-80.519375, 43.460311)
NORTHFIELD_STATION = (-80.544303, 43.497853)
THE_BOARDWALK = (-80.560737, 43.435320)
VICTORIA_PARK = (-80.498274, 43.446418)
KITCHENER_MARKET = (-80.483738, 43.446358)
UNIVERSITY_DOWNS_PARK = (-80.489416, 43.489872)
BECHTEL_PARK = (-80.492636, 43.481629)
# From the earlier 20-case corpus (`waterloo_cases`), kept so the two meet.
CONESTOGA_MALL = (-80.5262, 43.4998)
RIM_PARK = (-80.4790, 43.5060)
UPTOWN_SQUARE = (-80.5222, 43.4650)
UPTOWN_TRANSIT_HUB = (-80.5228, 43.4664)


def _case(
    key: str,
    description: str,
    categories: tuple[str, ...],
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> AuditCase:
    return AuditCase(key, description, categories, origin, destination)


TRUTH_CORPUS: tuple[AuditCase, ...] = (
    # --- Campus -----------------------------------------------------------------
    _case("campus-dc-to-slc", "Davis Centre Library to the Student Life Centre",
          ("campus", "stairs"), DC_LIBRARY, STUDENT_LIFE_CENTRE),
    _case("campus-mc-to-e5", "Mathematics & Computer to Engineering 5",
          ("campus",), MATHEMATICS_AND_COMPUTER, ENGINEERING_5),
    _case("campus-pac-to-dp", "Physical Activities Complex to Dana Porter Library",
          ("campus",), PHYSICAL_ACTIVITIES_COMPLEX, DANA_PORTER_LIBRARY),
    _case("campus-ev3-to-e3", "Environment 3 to Engineering 3",
          ("campus",), ENVIRONMENT_3, ENGINEERING_3),
    _case("campus-rev-to-mc", "Ron Eydt Village across Ring Road to Mathematics & Computer",
          ("campus",), RON_EYDT_VILLAGE, MATHEMATICS_AND_COMPUTER),
    _case("campus-ml-to-sch", "Modern Languages to South Campus Hall",
          ("campus",), MODERN_LANGUAGES, SOUTH_CAMPUS_HALL),
    _case("campus-stj-to-ren", "St. Jerome's to Renison, next door",
          ("campus", "identical"), ST_JEROMES, RENISON),
    _case("campus-cif-to-fed", "Columbia Icefield to Federation Hall",
          ("campus", "missing_surface"), COLUMBIA_ICEFIELD, FEDERATION_HALL),
    _case("campus-grebel-to-slc", "Conrad Grebel to the Student Life Centre",
          ("campus",), CONRAD_GREBEL, STUDENT_LIFE_CENTRE),
    _case("campus-station-to-dc", "University of Waterloo ION station to the Davis Centre",
          ("campus",), UW_STATION, DC_LIBRARY),
    _case("campus-phy-to-c2", "Physics to Chemistry 2",
          ("campus",), PHYSICS, CHEMISTRY_2),
    _case("campus-b1-to-cph", "Biology 1 to Carl A. Pollock Hall",
          ("campus",), BIOLOGY_1, CARL_POLLOCK_HALL),
    # --- Longer journeys across Waterloo and Kitchener ---------------------------
    _case("long-dc-to-laurier", "Davis Centre to Laurier's Fred Nichols Campus Centre",
          ("long",), DC_LIBRARY, FRED_NICHOLS_CENTRE),
    _case("long-laurier-to-square", "Laurier down to Waterloo Public Square",
          ("long",), FRED_NICHOLS_CENTRE, WATERLOO_PUBLIC_SQUARE),
    _case("long-square-to-park", "Waterloo Public Square to Waterloo Park",
          ("long", "missing_surface"), WATERLOO_PUBLIC_SQUARE, WATERLOO_PARK),
    _case("long-hospital-to-city-hall", "Grand River Hospital to Kitchener City Hall",
          ("long",), GRAND_RIVER_HOSPITAL, KITCHENER_CITY_HALL),
    _case("long-belmont-to-allen", "Belmont Village to Allen station",
          ("long",), BELMONT_VILLAGE, ALLEN_STATION),
    _case("long-northfield-to-icefield", "Northfield station to Columbia Icefield",
          ("long",), NORTHFIELD_STATION, COLUMBIA_ICEFIELD),
    _case("long-boardwalk-to-rev", "The Boardwalk to Ron Eydt Village, about four kilometres",
          ("long",), THE_BOARDWALK, RON_EYDT_VILLAGE),
    _case("long-square-to-hospital", "Waterloo Public Square to Grand River Hospital",
          ("long",), WATERLOO_PUBLIC_SQUARE, GRAND_RIVER_HOSPITAL),
    _case("long-victoria-park-to-market", "Victoria Park to Kitchener Market",
          ("long",), VICTORIA_PARK, KITCHENER_MARKET),
    _case("long-downs-to-bechtel", "University Downs Park to Bechtel Park",
          ("long", "missing_surface"), UNIVERSITY_DOWNS_PARK, BECHTEL_PARK),
    _case("long-park-to-laurier", "Waterloo Park to Laurier",
          ("long", "missing_surface"), WATERLOO_PARK, FRED_NICHOLS_CENTRE),
    # --- Stairways: bottom to top of the longest recorded stairways --------------
    _case("stairs-700726011", "Campus stairway, 71 m, bottom to top",
          ("stairs", "large_detour"), (-80.549346, 43.471175), (-80.549858, 43.471515)),
    _case("stairs-744204757", "Stairway by Kitchener City Hall, 6 recorded steps",
          ("stairs", "large_detour"), (-80.492286, 43.452053), (-80.492607, 43.452188)),
    _case("stairs-1536295321", "Stairway by Columbia Icefield, 29 m",
          ("stairs", "large_detour"), (-80.545768, 43.475599), (-80.546097, 43.475488)),
    _case("stairs-797093095", "Stairway in north-east Waterloo, 41 m",
          ("stairs", "large_detour"), (-80.508790, 43.476875), (-80.508447, 43.476700)),
    # --- Gradients: end to end of the longest footpaths steeper than 8% ----------
    _case("gradient-40585154-up", "Steep path in south Kitchener, one way",
          ("gradient",), (-80.531191, 43.438097), (-80.537267, 43.437598)),
    _case("gradient-40585154-down", "The same steep path, the other way",
          ("gradient",), (-80.537267, 43.437598), (-80.531191, 43.438097)),
    _case("gradient-34149352", "Steep path in south-east Kitchener",
          ("gradient",), (-80.491571, 43.420131), (-80.493872, 43.421460)),
    _case("gradient-1369397887", "Steep path in west Waterloo",
          ("gradient",), (-80.587935, 43.456329), (-80.585330, 43.460910)),
    # --- Crossings and kerbs: across King Street uptown --------------------------
    _case("crossing-648928668-two-kerbs", "Uptown crossing with both kerbs recorded",
          ("crossings",), (-80.524991, 43.464340), (-80.525191, 43.464224)),
    _case("crossing-1105419114-no-kerbs", "Uptown crossing with no kerb recorded",
          ("crossings",), (-80.522704, 43.466072), (-80.522658, 43.465917)),
    _case("crossing-682330817-four-kerbs", "Uptown crossing in six pieces, four kerb records",
          ("crossings",), (-80.522905, 43.467551), (-80.523135, 43.467520)),
    _case("crossing-uptown-to-hub", "Uptown square to the transit hub, several crossings",
          ("crossings",), UPTOWN_SQUARE, UPTOWN_TRANSIT_HUB),
    # --- foot=no ------------------------------------------------------------------
    _case("foot-no-1204792959-ends", "End to end of a road where pedestrians are prohibited",
          ("foot_no",), (-80.566929, 43.482907), (-80.572270, 43.474894)),
    _case("foot-no-1204792959-on-it", "Starting on that road, to Columbia Icefield",
          ("foot_no", "snapping"), (-80.570296, 43.479329), COLUMBIA_ICEFIELD),
    # --- Snapping -----------------------------------------------------------------
    _case("snap-mall-parking", "From the middle of Conestoga Mall's parking to Northfield",
          ("snapping",), CONESTOGA_MALL, NORTHFIELD_STATION),
    _case("snap-rim-park", "Conestoga Mall to RIM Park",
          ("snapping", "long"), CONESTOGA_MALL, RIM_PARK),
    _case("snap-inside-dc", "From the middle of the Davis Centre building to MC",
          ("snapping", "campus"), DC_BUILDING, MATHEMATICS_AND_COMPUTER),
    _case("snap-open-field", "From open farmland at the region's north-west edge",
          ("snapping",), (-80.5850, 43.5100), NORTHFIELD_STATION),
)  # fmt: skip
