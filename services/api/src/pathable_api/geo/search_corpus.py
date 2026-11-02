"""What people type to find a place in Waterloo, and what they mean by it.

The search-quality corpus `pathable gazetteer evaluate` runs. Each query names
the OpenStreetMap element(s) a person typing it is looking for and how far down
the results it may appear. Fixed before the version-2 index was evaluated, and
versioned: changing a query or an expectation is a new corpus version.

Expectations are the place a person means, not what the index happens to
return. A query the index cannot answer stays in the corpus and is reported as
a miss — "DP", the name students use for the Dana Porter Library, is recorded
nowhere in OpenStreetMap, and nothing here pretends otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bumped whenever a query or an expectation changes.
SEARCH_CORPUS_VERSION = 1

#: (osm_type, osm_id)
Element = tuple[str, int]


@dataclass(frozen=True, slots=True)
class SearchCase:
    query: str
    #: What the query is testing: code, name, partial, misspelling, spelling,
    #: address, street, transit.
    category: str
    #: Any of these elements is the place meant.
    expected: tuple[Element, ...]
    #: The first of them must appear at this position or better (1 is first).
    within: int
    meaning: str


DC = ("relation", 8765264)
DC_LIBRARY = ("node", 1505832285)
SLC = ("way", 43250627)
MC = ("way", 43250630)
E5 = ("way", 51125806)
PAC = ("way", 43250596)
QNC = ("way", 182091547)
EV3 = ("way", 167411346)
DANA_PORTER = ("way", 43250626)
FED_HALL = ("way", 43250640)
UW = ("relation", 11680616)
UW_STATION = ("node", 4211159080)
CONESTOGA_MALL = ("way", 165512577)
HOSPITAL = (("relation", 18980846), ("way", 140180455))
PUBLIC_SQUARE = ("way", 62299949)
KITCHENER_CITY_HALL = ("way", 138175760)
WATERLOO_CITY_CENTRE = ("way", 43250653)
LAURIER = ("way", 226405688)
WATERLOO_PUBLIC_LIBRARY = ("way", 43251385)
CAMPUS_ADDRESS = (("node", 11541059296), ("way", 258817910))
KING_STREET_NORTH = ("way", 193617706)
COLUMBIA_STREET_WEST = ("way", 42684286)
RING_ROAD = ("way", 24636861)


def _case(
    query: str, category: str, expected: tuple[Element, ...], within: int, meaning: str
) -> SearchCase:
    return SearchCase(query, category, expected, within, meaning)


SEARCH_CORPUS: tuple[SearchCase, ...] = (
    # --- Building codes ---------------------------------------------------------
    _case("DC", "code", (DC,), 1, "the Davis Centre building"),
    _case("dc", "code", (DC,), 1, "the Davis Centre, typed in lower case"),
    _case("SLC", "code", (SLC,), 1, "the Student Life Centre"),
    _case("MC", "code", (MC,), 1, "the Mathematics & Computer building"),
    _case("E5", "code", (E5,), 1, "Engineering 5"),
    _case("PAC", "code", (PAC,), 1, "the Physical Activities Complex"),
    _case("QNC", "code", (QNC,), 1, "the Quantum-Nano Centre"),
    _case("EV3", "code", (EV3,), 1, "Environment 3"),
    _case("UW", "code", (UW,), 1, "the University of Waterloo"),
    _case("DP", "code", (DANA_PORTER,), 3, "the Dana Porter Library, by its campus nickname"),
    # --- Names ------------------------------------------------------------------
    _case("Davis", "name", (DC, DC_LIBRARY), 2, "the Davis Centre or its library"),
    _case("Davis Centre", "name", (DC, DC_LIBRARY), 2, "the Davis Centre or its library"),
    _case("University of Waterloo", "name", (UW,), 1, "the campus"),
    _case("Student Life Centre", "name", (SLC,), 1, "the Student Life Centre"),
    _case("Math and Computer", "name", (MC,), 1, "MC, with 'and' for '&'"),
    _case("Dana Porter", "name", (DANA_PORTER,), 1, "the Dana Porter Library"),
    _case("Federation Hall", "name", (FED_HALL,), 1, "Fed Hall"),
    _case("Conestoga Mall", "name", (CONESTOGA_MALL,), 1, "the mall"),
    _case("Waterloo Public Square", "name", (PUBLIC_SQUARE,), 1, "the square, not its stop"),
    _case("Kitchener City Hall", "name", (KITCHENER_CITY_HALL,), 2, "the city hall, not its stop"),
    _case("Waterloo Public Library", "name", (WATERLOO_PUBLIC_LIBRARY,), 3, "the main branch"),
    _case("Wilfrid Laurier University", "name", (LAURIER,), 3, "Laurier's Waterloo campus"),
    _case("Grand River Hospital", "name", HOSPITAL, 3, "the hospital, renamed WRHN @ Midtown"),
    # --- Partial names, as they are typed -----------------------------------------
    _case("Davi", "partial", (DC, DC_LIBRARY), 3, "Davis, half typed"),
    _case("Student L", "partial", (SLC,), 1, "the Student Life Centre, half typed"),
    _case("Conest", "partial", (CONESTOGA_MALL,), 3, "Conestoga Mall, half typed"),
    _case("Physical Act", "partial", (PAC,), 1, "the PAC, half typed"),
    _case("sl", "partial", (SLC,), 5, "SLC, two letters in"),
    # --- Misspellings and spelling variants -------------------------------------------
    _case("Davis Center", "spelling", (DC, DC_LIBRARY), 2, "the Davis Centre, US spelling"),
    _case("Student Life Center", "spelling", (SLC,), 1, "the SLC, US spelling"),
    _case("Konestoga Mall", "misspelling", (CONESTOGA_MALL,), 3, "Conestoga, misspelled"),
    _case("Unversity of Waterloo", "misspelling", (UW,), 3, "University, a letter dropped"),
    _case("Federaton Hall", "misspelling", (FED_HALL,), 3, "Federation, a letter dropped"),
    # --- Addresses and streets ------------------------------------------------------
    _case("200 University Ave W", "address", CAMPUS_ADDRESS, 1, "the campus's street address"),
    _case("100 Regina St S", "address", (WATERLOO_CITY_CENTRE,), 3, "Waterloo City Centre"),
    _case("550 King St N", "address", (CONESTOGA_MALL,), 3, "Conestoga Mall's address"),
    _case("King Street North", "street", (KING_STREET_NORTH,), 3, "King Street North, Waterloo"),
    _case("Columbia St W", "street", (COLUMBIA_STREET_WEST,), 3, "Columbia Street West"),
    _case("Ring Road", "street", (RING_ROAD,), 3, "the campus ring road"),
    # --- Transit ---------------------------------------------------------------------
    _case("University of Waterloo station", "transit", (UW_STATION,), 3, "the campus ION station"),
)
