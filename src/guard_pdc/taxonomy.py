"""Stable PDC labels, groups, and ordering used by query and data contracts."""

from __future__ import annotations

from collections.abc import Iterable


CATEGORY_ORDER = (
    "people",
    "children_0_4",
    "children_5_9",
    "children_10_14",
    "children_15_19",
    "adult_20_24",
    "adult_25_29",
    "adult_30_34",
    "adult_35_39",
    "adult_40_44",
    "adult_45_49",
    "adult_50_54",
    "adult_55_59",
    "adult_60_64",
    "elderly",
    "households",
    "global_currency",
    "schools",
    "hospitals",
)

KNOWN_CATEGORIES = frozenset(CATEGORY_ORDER)
AGE_BAND_CATEGORIES = CATEGORY_ORDER[1:15]
IMPACT_TYPE_ORDER = ("affected_total", "cost")
KNOWN_IMPACT_TYPES = frozenset(IMPACT_TYPE_ORDER)

CATEGORY_LABELS = {
    "people": "People",
    "children_0_4": "Children 0–4",
    "children_5_9": "Children 5–9",
    "children_10_14": "Children 10–14",
    "children_15_19": "Children 15–19",
    "adult_20_24": "Adults 20–24",
    "adult_25_29": "Adults 25–29",
    "adult_30_34": "Adults 30–34",
    "adult_35_39": "Adults 35–39",
    "adult_40_44": "Adults 40–44",
    "adult_45_49": "Adults 45–49",
    "adult_50_54": "Adults 50–54",
    "adult_55_59": "Adults 55–59",
    "adult_60_64": "Adults 60–64",
    "elderly": "Elderly",
    "households": "Households",
    "global_currency": "Global currency",
    "schools": "Schools",
    "hospitals": "Hospitals",
}

CATEGORY_GROUPS = {
    "people": "Population",
    **{category: "Age" for category in AGE_BAND_CATEGORIES},
    "elderly": "Age",
    "households": "Households",
    "global_currency": "Capital",
    "schools": "Facilities",
    "hospitals": "Facilities",
}

IMPACT_TYPE_LABELS = {
    "affected_total": "Affected total",
    "cost": "Cost",
}

# Categories each PDC impact type carries in every observed production item
# (PHL/BGD/NPL 2024 and the global Q4-2025 sample). Status rows are built only
# for these pairs plus any pair actually observed, so "cost x people" is never
# reported as missing evidence.
TYPE_CATEGORIES = {
    "affected_total": tuple(category for category in CATEGORY_ORDER if category != "global_currency"),
    "cost": ("global_currency",),
}

# Analyst-facing measures. Each maps to exact impact type/category pairs; the
# selection only narrows retrieval and never sums categories.
MEASURES = {
    "people": ("People", ("affected_total",), ("people",)),
    "households": ("Households", ("affected_total",), ("households",)),
    "schools": ("Schools", ("affected_total",), ("schools",)),
    "hospitals": ("Hospitals", ("affected_total",), ("hospitals",)),
    "capital": ("Capital exposure", ("cost",), ("global_currency",)),
    "age_groups": ("Age groups", ("affected_total",), AGE_BAND_CATEGORIES),
}
DEFAULT_MEASURES = ("people", "households", "schools", "hospitals", "capital")
MEASURE_CATEGORY = {"people": "people", "households": "households", "schools": "schools", "hospitals": "hospitals", "capital": "global_currency"}
CAPITAL_UNIT_NOTE = "Unit not stated in the structured PDC data"


def measures_query(measures: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Exact impact types and categories for a measure selection."""

    selected = [MEASURES[measure] for measure in measures]
    types = ordered_impact_types(value for _, impact_types, _ in selected for value in impact_types)
    categories = ordered_categories(value for _, _, values in selected for value in values)
    return types, categories

# PDC hazard types and the code triples the pystac-monty PDC transformer emits
# for them (UNDRR-ISC 2025, EM-DAT, GLIDE). Exact returned codes remain on
# every row; these tables only provide readable labels and filter options.
PDC_HAZARD_TYPES = {
    "AVALANCHE": ("Avalanche", ("MH0801", "nat-hyd-mmw-ava", "AV")),
    "BIOMEDICAL": ("Biomedical", ("BI0101", "nat-bio-epi-dis", "EP")),
    "DROUGHT": ("Drought", ("MH0401", "nat-cli-dro-dro", "DR")),
    "EARTHQUAKE": ("Earthquake", ("GH0101", "nat-geo-ear-gro", "EQ")),
    "EXTREMETEMPERATURE": ("Extreme temperature", ("MH0501", "nat-met-ext-hea", "HT")),
    "FLOOD": ("Flood", ("MH0600", "nat-hyd-flo-flo", "FL")),
    "HIGHSURF": ("High surf", ("MH0702", "nat-hyd-wav-wav", "OT")),
    "LANDSLIDE": ("Landslide", ("GH0300", "nat-geo-mmd-lan", "LS")),
    "MARINE": ("Marine", ("MH0700", "nat-hyd-wav-wav", "OT")),
    "SEVEREWEATHER": ("Severe weather", ("MH0103", "nat-met-sto-sto", "ST")),
    "STORM": ("Storm", ("MH0103", "nat-met-sto-sto", "ST")),
    "TORNADO": ("Tornado", ("MH0305", "nat-met-sto-tor", "TO")),
    "CYCLONE": ("Tropical cyclone", ("MH0306", "nat-met-sto-tro", "TC")),
    "TSUNAMI": ("Tsunami", ("MH0705", "nat-geo-ear-tsu", "TS")),
    "VOLCANO": ("Volcano", ("GH0201", "nat-geo-vol-vol", "VO")),
    "WILDFIRE": ("Wildfire", ("EN0205", "nat-cli-wil-wil", "WF")),
    "WINTERSTORM": ("Winter storm", ("MH0403", "nat-met-sto-bli", "OT")),
    "HIGHWIND": ("High wind", ("MH0301", "nat-met-sto-sto", "VW")),
}

# Display groups in fixed colour-slot order (theme.HAZARD_COLORS). Codes that
# several PDC types share with unrelated hazards (GLIDE "OT") are not listed,
# so a server-side a_overlaps filter never pulls in another group.
FLOOD_CODES = ("MH0600", "FL", "nat-hyd-flo-flo")
OTHER_HAZARD_GROUP = "Other"
HAZARD_GROUPS = (
    ("Flood", FLOOD_CODES),
    ("Landslide & avalanche", ("GH0300", "LS", "nat-geo-mmd-lan", "MH0801", "AV", "nat-hyd-mmw-ava")),
    ("Tropical cyclone", ("MH0306", "TC", "nat-met-sto-tro")),
    ("Storm & winter weather", ("MH0103", "ST", "nat-met-sto-sto", "MH0305", "TO", "nat-met-sto-tor", "MH0301", "VW", "MH0403", "nat-met-sto-bli")),
    ("Drought & extreme temperature", ("MH0401", "DR", "nat-cli-dro-dro", "MH0501", "HT", "nat-met-ext-hea")),
    ("Wildfire", ("EN0205", "WF", "nat-cli-wil-wil")),
    ("Earthquake & tsunami", ("GH0101", "EQ", "nat-geo-ear-gro", "MH0705", "TS", "nat-geo-ear-tsu")),
    ("Volcano", ("GH0201", "VO", "nat-geo-vol-vol")),
)
HAZARD_GROUP_ORDER = tuple(label for label, _ in HAZARD_GROUPS) + (OTHER_HAZARD_GROUP,)
HAZARD_LABELS = {code: label for label, codes in HAZARD_GROUPS for code in codes}
HAZARD_OPTIONS = HAZARD_GROUPS
KNOWN_HAZARD_CODES = tuple(code for _, codes in HAZARD_GROUPS for code in codes)

# PDC title prefixes are finer than the code triples ("Coastal Flood",
# "Snow Squall"). Normalise only spelling variants; never re-classify.
_TITLE_TYPE_ALIASES = {"Floods": "Flood", "Landslides": "Landslide", "Storms": "Storm"}


def category_label(category: str) -> str:
    """Return a readable label without discarding an uncovered category."""

    return CATEGORY_LABELS.get(category, category.replace("_", " ").title())


def category_group(category: str) -> str:
    """Return the display group, keeping new source categories visible."""

    return CATEGORY_GROUPS.get(category, "Other")


def impact_type_label(impact_type: str) -> str:
    return IMPACT_TYPE_LABELS.get(impact_type, impact_type.replace("_", " ").title())


def hazard_label(code: str) -> str:
    """Map a known code to one label; unknown codes remain identifiable."""

    return HAZARD_LABELS.get(code, code)


def hazard_groups(codes: Iterable[str]) -> tuple[str, ...]:
    """Display groups for one item's code set, in group order; never empty."""

    found = {HAZARD_LABELS[code] for code in codes if code in HAZARD_LABELS}
    return tuple(label for label in HAZARD_GROUP_ORDER if label in found) or (OTHER_HAZARD_GROUP,)


def hazard_group(codes: Iterable[str]) -> str:
    """One primary display group (first in group order) for colour and legends."""

    return hazard_groups(codes)[0]


def code_title_caveat(title: str | None, codes: Iterable[str]) -> str | None:
    """Explain known disagreements between PDC wording and the emitted codes.

    The source codes are never changed; this only stops a reader taking a
    code-derived label at face value.
    """

    text = (title or "").lower()
    code_set = set(codes)
    if "cold" in text and code_set & {"HT", "nat-met-ext-hea"}:
        return "PDC title says cold, but the codes are heat-wave codes (the transformer maps every PDC extreme-temperature event to heat)."
    if "fog" in text and code_set & {"ST", "nat-met-sto-sto"}:
        return "PDC title says fog, but the codes are storm codes (PDC severe-weather mapping)."
    if "tsunami" in text and "(" in (title or "").split(" - ", 1)[0]:
        return "Tsunami bulletin for a warning region; one earthquake can produce several bulletins, and the point is the bulletin location."
    return None


def pdc_hazard_type(title: str | None) -> str | None:
    """PDC's own hazard wording from the title prefix, e.g. 'Coastal Flood'."""

    if not isinstance(title, str) or not title.strip():
        return None
    prefix = title.split(" - ", 1)[0].strip()
    prefix = prefix.split(" (", 1)[0].strip()
    return _TITLE_TYPE_ALIASES.get(prefix, prefix) or None


def hazard_codes_for_label(label: str) -> tuple[str, ...]:
    for option_label, codes in HAZARD_OPTIONS:
        if option_label == label:
            return codes
    raise ValueError(f"Unknown hazard label: {label}")


def ordered_categories(categories: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate categories in contract order, then stable-sort new ones."""

    unique = set(categories)
    known = [category for category in CATEGORY_ORDER if category in unique]
    unknown = sorted(unique.difference(KNOWN_CATEGORIES))
    return tuple(known + unknown)


def ordered_impact_types(impact_types: Iterable[str]) -> tuple[str, ...]:
    unique = set(impact_types)
    known = [impact_type for impact_type in IMPACT_TYPE_ORDER if impact_type in unique]
    unknown = sorted(unique.difference(KNOWN_IMPACT_TYPES))
    return tuple(known + unknown)


def ordered_hazard_codes(codes: Iterable[str]) -> tuple[str, ...]:
    unique = set(codes)
    known = [code for code in KNOWN_HAZARD_CODES if code in unique]
    unknown = sorted(unique.difference(KNOWN_HAZARD_CODES))
    return tuple(known + unknown)


def unknown_categories(categories: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(categories).difference(KNOWN_CATEGORIES)))


def unknown_impact_types(impact_types: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(impact_types).difference(KNOWN_IMPACT_TYPES)))
