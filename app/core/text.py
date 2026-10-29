"""
Shared input normalization and formatting helpers.

Framework-agnostic string/date helpers used by the core operations.
"""

from datetime import datetime
from typing import Optional


def clean(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return clean(value).lower() in {"true", "1", "yes"}


def normalize_equipment(value: Optional[str]) -> str:
    if not value:
        return ""
    return (
        str(value).strip().lower()
        .replace("-", "").replace("_", "").replace(" ", "")
    )


def equipment_to_tms(value: Optional[str]) -> str:
    """
    Convert production voice workflow/user wording into TMS identifiers.
        dry van -> DRY_VAN
        reefer  -> REEFER
        flatbed -> FLATBED
    """
    if not value:
        return ""

    normalized = (
        str(value).strip().upper()
        .replace("-", "_").replace(" ", "_")
    )

    aliases = {
        "DRYVAN": "DRY_VAN",
        "DRY_VAN": "DRY_VAN",
        "VAN": "DRY_VAN",
        "REEFER": "REEFER",
        "REFRIGERATED": "REEFER",
        "FLATBED": "FLATBED",
        "FLAT_BED": "FLATBED",
    }

    return aliases.get(normalized, normalized)


US_STATES = {
    # English
    "alabama": "al",
    "alaska": "ak",
    "arizona": "az",
    "arkansas": "ar",
    "california": "ca",
    "colorado": "co",
    "connecticut": "ct",
    "delaware": "de",
    "florida": "fl",
    "georgia": "ga",
    "hawaii": "hi",
    "idaho": "id",
    "illinois": "il",
    "indiana": "in",
    "iowa": "ia",
    "kansas": "ks",
    "kentucky": "ky",
    "louisiana": "la",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "michigan": "mi",
    "minnesota": "mn",
    "mississippi": "ms",
    "missouri": "mo",
    "montana": "mt",
    "nebraska": "ne",
    "nevada": "nv",
    "new hampshire": "nh",
    "new jersey": "nj",
    "new mexico": "nm",
    "new york": "ny",
    "nueva york": "ny",
    "north carolina": "nc",
    "north dakota": "nd",
    "ohio": "oh",
    "oklahoma": "ok",
    "oregon": "or",
    "pennsylvania": "pa",
    "rhode island": "ri",
    "south carolina": "sc",
    "south dakota": "sd",
    "tennessee": "tn",
    "texas": "tx",
    "utah": "ut",
    "vermont": "vt",
    "virginia": "va",
    "washington": "wa",
    "west virginia": "wv",
    "wisconsin": "wi",
    "wyoming": "wy",

    # Spanish
    "alabama": "al",
    "alaska": "ak",
    "arizona": "az",
    "arkansas": "ar",
    "california": "ca",
    "colorado": "co",
    "connecticut": "ct",
    "delaware": "de",
    "florida": "fl",
    "georgia": "ga",
    "hawai": "hi",
    "idaho": "id",
    "illinois": "il",
    "indiana": "in",
    "iowa": "ia",
    "kansas": "ks",
    "kentucky": "ky",
    "luisiana": "la",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "míchigan": "mi",
    "minnesota": "mn",
    "misisipi": "ms",
    "misuri": "mo",
    "montana": "mt",
    "nebraska": "ne",
    "nevada": "nv",
    "nuevo hampshire": "nh",
    "nueva jersey": "nj",
    "nuevo méxico": "nm",
    "carolina del norte": "nc",
    "dakota del norte": "nd",
    "ohio": "oh",
    "oklahoma": "ok",
    "oregon": "or",
    "pensilvania": "pa",
    "rhode island": "ri",
    "carolina del sur": "sc",
    "dakota del sur": "sd",
    "tennessee": "tn",
    "texas": "tx",
    "utah": "ut",
    "vermont": "vt",
    "virginia": "va",
    "washington": "wa",
    "virginia occidental": "wv",
    "wisconsin": "wi",
    "wyoming": "wy",
}


def split_city_state(value: Optional[str]) -> tuple[str, str]:
    """'Chicago, Illinois' -> ('Chicago', 'IL'). State is '' if not given."""
    value = clean(value)
    if not value:
        return "", ""
    if "," not in value:
        return value, ""
    city, state = value.rsplit(",", 1)
    state = state.strip()
    abbrev = state.upper() if len(state) == 2 else US_STATES.get(state.lower(), "")
    return city.strip(), abbrev


def to_int(value) -> Optional[int]:
    try:
        return int(float(clean(value)))
    except (ValueError, TypeError):
        return None


def format_tms_datetime(value: str) -> str:
    """20250101083000 -> 2025-01-01 08:30. Other formats pass through."""
    if len(value) == 14:
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return value
