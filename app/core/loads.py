"""
Load search, filtering, and agent-safe formatting.

The single implementation of the /search-loads business operation:
test loads (adversarial lanes) first, then a live TMS LOAD_QUERY,
with state-level filtering and MAX_BUY-free formatting.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.core import config
from app.core.text import (
    clean,
    equipment_to_tms,
    format_tms_datetime,
    split_city_state,
    to_int,
)
from app.core.tms import tms_get_load, tms_request


def format_load(load_id: str, orig_city, orig_state, dest_city, dest_state,
                pickup, delivery, equipment, weight, commodity, pieces,
                miles, dims, rate, notes) -> dict:
    """Agent-facing load. MAX_BUY is NEVER included."""
    lane = f"{orig_city}, {orig_state} → {dest_city}, {dest_state}"
    if delivery:
        dates = f"Pickup: {pickup}; Delivery: {delivery}"
    else:
        dates = f"Pickup: {pickup}"
    return {
        "load_id": load_id,
        "lane": lane,
        "dates": dates,
        "equipment": equipment,
        "weight": to_int(weight),
        "commodity": commodity,
        "num_of_pieces": to_int(pieces),
        "miles": to_int(miles),
        "dimensions": dims,
        "posted_rate": to_int(rate),
        "notes": notes,
    }


def find_test_loads(orig_city, orig_state, dest_city, dest_state,
                    equipment, excluded) -> list[dict]:
    if not config.ENABLE_TEST_LOADS:
        return []

    eq = equipment_to_tms(equipment) if equipment else ""
    pickup = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d 08:00")
    delivery = (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d 17:00")

    results = []
    for load_id, load in config.TEST_LOADS.items():
        if load_id in excluded:
            continue
        if orig_city and load["orig_city"].lower() != orig_city.lower():
            continue
        if orig_state and load["orig_state"] != orig_state:
            continue
        if dest_city and load["dest_city"].lower() != dest_city.lower():
            continue
        if dest_state and load["dest_state"] != dest_state:
            continue
        if eq and load["eqtype"] != eq:
            continue
        results.append(
            format_load(
                load_id, load["orig_city"], load["orig_state"],
                load["dest_city"], load["dest_state"], pickup, delivery,
                load["eqtype"], load["weight"], load["commodity"],
                load["pieces"], load["miles"], load["dims"],
                load["rate"], load["notes"],
            )
        )
    return results


@dataclass
class LoadQuery:
    """
    One load search, as the core layer sees it.

    The two protocol adapters build it from their own argument
    shapes (the HTTP request model, the MCP tool's flat parameters)
    and hand it to search_loads, so the five search params travel
    as one named value instead of positionally. Fields the adapters
    may leave unset are Optional — clean() normalizes them.
    """
    origin: Optional[str]
    destination: Optional[str]
    equipment_type: Optional[str]
    pickup_date: Optional[str]
    exclude_load_ids: Optional[str]


def search_loads(query: LoadQuery) -> dict:
    """Search loads; returns the /search-loads response dict."""
    origin = clean(query.origin)
    destination = clean(query.destination)
    equipment = clean(query.equipment_type)
    pickup_date = clean(query.pickup_date)

    # TMS requires at least one filter.
    if not origin and not destination and not equipment and not pickup_date:
        return {"loads": [], "reason": "missing_search_criteria"}

    query_orig_city, orig_state_filter = split_city_state(origin)
    query_dest_city, dest_state_filter = split_city_state(destination)

    excluded = {
        item.strip()
        for item in clean(query.exclude_load_ids).split(",")
        if item.strip()
    }

    # Test loads first, so adversarial tests are deterministic.
    test_matches = find_test_loads(
        query_orig_city, orig_state_filter,
        query_dest_city, dest_state_filter,
        equipment, excluded,
    )
    if test_matches:
        print("[TEST SEARCH]", origin, equipment, [l["load_id"] for l in test_matches])
        return {"loads": test_matches}

    # --------------------------------------------------------
    # Build LOAD_QUERY
    #
    # The TMS stores city and state separately, so only the
    # city goes to the TMS. State is filtered below.
    # --------------------------------------------------------

    fields = {}

    if query_orig_city:
        fields["ORIG_CITY"] = query_orig_city

    if query_dest_city:
        fields["DEST_CITY"] = query_dest_city

    if equipment:
        fields["EQTYPE"] = equipment_to_tms(equipment)

    if pickup_date:
        fields["PICKUP_DT"] = pickup_date

    fields["MAX_RESULTS"] = "5"

    try:
        records = tms_request("LOAD_QUERY", fields)
    except Exception as exc:
        print(f"[TMS SEARCH ERROR] {exc}")
        return {"loads": [], "reason": "tms_error"}

    loads = []

    for query_record in records:
        load_id = clean(query_record.get("load_id"))

        if not load_id or load_id in excluded:
            continue

        status = clean(query_record.get("status")).upper()
        if status and status != "OPEN":
            continue

        # Full record, with MAX_BUY already removed.
        full_record = tms_get_load(load_id)
        if full_record is None:
            # Don't expose a partial record to the agent.
            continue

        def field(name: str, fallback_to_query: bool = True) -> str:
            default = query_record.get(name) if fallback_to_query else ""
            return clean(full_record.get(name, default))

        orig_city = field("orig_city")
        orig_state = field("orig_state")
        dest_city = field("dest_city")
        dest_state = field("dest_state")

        # State filter: skip loads from the wrong state
        # (e.g. Chicago Heights, IN when the caller said Illinois).
        if orig_state_filter and orig_state and orig_state.upper() != orig_state_filter:
            continue

        if dest_state_filter and dest_state and dest_state.upper() != dest_state_filter:
            continue

        loads.append(
            format_load(
                load_id, orig_city, orig_state, dest_city, dest_state,
                format_tms_datetime(field("pickup_dt")),
                format_tms_datetime(field("delivery_dt", False)),
                field("eqtype"), field("weight", False),
                field("commodity", False), field("pieces", False),
                field("miles"), field("dims", False), field("rate"),
                field("notes", False),
            )
        )

    return {"loads": loads}
