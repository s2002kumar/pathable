"""How place names and search queries are compared.

One function for both sides. Stored names and typed queries pass through the
same normalisation, so "200 University Ave W" and "200 University Avenue West"
meet in the middle — and so a change here changes both at once, which is why an
index records the gazetteer version that built it.

Kept apart from :mod:`pathable_api.geo.gazetteer` so the API can normalise a
query without importing the extract reader.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

#: Bumped whenever extraction or normalisation changes what an extract becomes,
#: so a stored index says which rules produced it, and an index built under
#: older rules can be told apart from a current one. Defined here rather than in
#: the extract reader so the API can compare versions without importing it.
#:
#: 2 — places carry the short codes OpenStreetMap records for them (`ref`,
#:     `short_name`: DC, SLC, E5) and the initials of their names, kept apart
#:     because one is recorded and the other generated; "center" and "centre"
#:     are folded together.
GAZETTEER_VERSION = 2

#: Written forms that mean the same thing. The rule is symmetry, not accuracy:
#: "St Jacobs" becomes "street jacobs" on both sides, which is ugly and still
#: matches.
ABBREVIATIONS: Mapping[str, str] = {
    "ave": "avenue",
    "av": "avenue",
    "blvd": "boulevard",
    "cir": "circle",
    "cres": "crescent",
    "ct": "court",
    "dr": "drive",
    "hwy": "highway",
    "ln": "lane",
    "pkwy": "parkway",
    "pl": "place",
    "rd": "road",
    "sq": "square",
    "st": "street",
    "terr": "terrace",
    "trl": "trail",
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    # Spellings, folded the same way: "Davis Center" is how half the people who
    # type it spell the Davis Centre, and neither spelling is wrong.
    "center": "centre",
    "theater": "theatre",
}

#: Words an initialism leaves out: "Faculty of Mathematics" is FM, not FOM.
_INITIALISM_SKIPS = frozenset({"of", "the", "and", "at", "on", "for", "in", "a", "an"})

#: What a building code looks like: one to four letters, then at most two
#: digits, at least two characters in all — DC, SLC, E5, EV3, PAC. A road
#: number, a stop number or a lone letter does not.
_CODE = re.compile(r"^(?=.{2,6}$)[a-z]{1,4}[0-9]{0,2}$")

#: Removed rather than turned into a space, so "Children's" stays one word
#: instead of becoming "children" and a stray "s" that would read as "south".
_APOSTROPHES = str.maketrans("", "", "'\u2019`")
_NON_WORD = re.compile(r"[^a-z0-9]+")

#: A query longer than this is not a place name; the rest is ignored.
MAX_QUERY_TERMS = 8


def normalise_text(text: str) -> str:
    """Lower-case, unaccented, punctuation-free, abbreviations expanded.

    The output contains only ``[a-z0-9]`` and single spaces, which is what lets
    the search build its patterns from it without escaping anything.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(character for character in decomposed if not unicodedata.combining(character))
    lowered = plain.lower().replace("&", " and ").translate(_APOSTROPHES)
    return " ".join(ABBREVIATIONS.get(token, token) for token in _NON_WORD.split(lowered) if token)


def query_terms(text: str) -> tuple[str, ...]:
    """The distinct normalised words of a query, in the order they were typed."""
    terms: list[str] = []
    for term in normalise_text(text).split():
        if term not in terms:
            terms.append(term)
    return tuple(terms[:MAX_QUERY_TERMS])


def code_of(value: str | None) -> str | None:
    """A recorded short code, normalised, or None when the value is not code-like.

    Codes are compared as written, so abbreviation expansion is not applied:
    the code "S" is not the word "south".
    """
    if value is None:
        return None
    compact = "".join(_NON_WORD.split(value.lower()))
    return compact if _CODE.fullmatch(compact) else None


def initialism(name: str) -> str | None:
    """The initials of a name, generated rather than recorded: "Student Life Centre" -> slc.

    A number stays whole, so "Engineering 5" gives e5. Small linking words are
    skipped. A one-word name has no initialism, and neither does one whose
    initials would be longer than a person would type.
    """
    words = [word for word in normalise_text(name).split() if word not in _INITIALISM_SKIPS]
    if len(words) < 2:
        return None
    letters = "".join(word if word.isdigit() else word[0] for word in words)
    return letters if 2 <= len(letters) <= 6 else None


def compact_query(text: str) -> str:
    """A query as a code or initialism is written: letters and digits, run together.

    From the text as typed, without abbreviations expanded — "St" stays st
    rather than becoming "street" — so "P.A.C." is pac and "E 5" is e5.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(character for character in decomposed if not unicodedata.combining(character))
    return "".join(_NON_WORD.split(plain.lower().translate(_APOSTROPHES)))


# --- What a result is -------------------------------------------------------------
#
# Many small features are named after the place they serve: the taxi stand at the
# Davis Centre is called "Davis Centre", the information board on campus is called
# "University of Waterloo". Matched on text alone they outrank the place itself.
# A tier puts them after it without hiding them.

#: Stops and stations carry the name of what they serve; a person searching for
#: the square usually wants the square, and the stop beside it is still offered.
TRANSIT_CATEGORIES: frozenset[str] = frozenset(
    {
        "transit stop",
        "transit station",
        "transit platform",
        "bus station",
        "station",
        "halt",
        "tram stop",
    }
)

#: Features that are rarely where somebody means to go, however well they match.
INCIDENTAL_CATEGORIES: frozenset[str] = frozenset(
    {
        "artwork",
        "atm",
        "bench",
        "bicycle parking",
        "charging station",
        "clock",
        "drinking water",
        "information",
        "motorcycle parking",
        "parcel locker",
        "parking",
        "parking entrance",
        "parking space",
        "post box",
        "recycling",
        "shelter",
        "taxi",
        "telephone",
        "toilets",
        "vending machine",
        "waste basket",
    }
)


def category_tier(category: str | None) -> int:
    """0 for a place someone is likely going to, 1 for transit, 2 for incidentals."""
    if category in INCIDENTAL_CATEGORIES:
        return 2
    if category in TRANSIT_CATEGORIES:
        return 1
    return 0
