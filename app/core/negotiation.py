"""
Round-limited negotiation engine.

The single implementation of the /evaluate-offer business operation.
The middleware owns the round count (request.round is never trusted
for enforcement) and the internal ceilings (NEGOTIATION_LIMITS) are
never revealed in any response.
"""

from app.core import config
from app.core.state import NEGOTIATION_STATE
from app.core.text import clean


def evaluate_offer(load_id, carrier_rate) -> dict:
    """Evaluate a carrier offer; returns the /evaluate-offer response dict."""
    load_id = clean(load_id)
    carrier_rate_raw = (
        clean(carrier_rate)
        .replace("$", "")
        .replace(",", "")
    )

    if not load_id or not carrier_rate_raw:
        return {
            "decision": "error",
            "next_rate": None,
            "reason": "missing_input",
        }

    try:
        carrier_rate = float(carrier_rate_raw)
    except ValueError:
        return {
            "decision": "error",
            "next_rate": None,
            "reason": "invalid_rate",
        }

    if carrier_rate <= 0:
        return {
            "decision": "error",
            "next_rate": None,
            "reason": "invalid_rate",
        }

    # ---------------------------------------------------------
    # Load must have a configured negotiation ceiling
    # ---------------------------------------------------------
    max_rate = config.NEGOTIATION_LIMITS.get(load_id)

    if max_rate is None:
        return {
            "decision": "error",
            "next_rate": None,
            "reason": "unknown_load",
        }

    # ---------------------------------------------------------
    # Get/create server-side negotiation state
    # ---------------------------------------------------------
    state = NEGOTIATION_STATE.setdefault(
        load_id,
        {
            "round": 0,
            "accepted": False,
            "agreed_rate": None,
        },
    )

    # ---------------------------------------------------------
    # Every carrier offer advances the negotiation round.
    #
    # Do NOT trust request.round for enforcement.
    # The middleware owns the round count.
    # ---------------------------------------------------------
    if state["round"] >= 3:
        state["accepted"] = False
        state["agreed_rate"] = None

        return {
            "decision": "reject",
            "next_rate": None,
            "reason": "negotiation_limit_reached",
            "round": state["round"],
        }

    state["round"] += 1

    # New carrier offer invalidates any previous acceptance.
    state["accepted"] = False
    state["agreed_rate"] = None

    negotiation_round = state["round"]

    print(
        f"[NEGOTIATION] "
        f"load={load_id} "
        f"round={negotiation_round} "
        f"carrier_rate={carrier_rate} "
        f"max_rate={max_rate}"
    )

    # ---------------------------------------------------------
    # Carrier offer is acceptable
    # ---------------------------------------------------------
    if carrier_rate <= max_rate:
        state["accepted"] = True
        state["agreed_rate"] = int(carrier_rate)

        return {
            "decision": "accept",
            "next_rate": int(carrier_rate),
            "round": negotiation_round,
        }

    # ---------------------------------------------------------
    # Carrier offer is above internal ceiling.
    #
    # Never reveal max_rate.
    # ---------------------------------------------------------
    if negotiation_round == 1:
        counter_rate = min(
            carrier_rate - 100,
            max_rate - 100,
        )

        if counter_rate <= 0:
            return {
                "decision": "reject",
                "next_rate": None,
                "round": negotiation_round,
            }

        return {
            "decision": "counter",
            "next_rate": int(counter_rate),
            "round": negotiation_round,
        }

    if negotiation_round == 2:
        counter_rate = max_rate - 50

        if counter_rate <= 0:
            return {
                "decision": "reject",
                "next_rate": None,
                "round": negotiation_round,
            }

        return {
            "decision": "final_counter",
            "next_rate": int(counter_rate),
            "round": negotiation_round,
        }

    # ---------------------------------------------------------
    # Round 3: no further negotiation.
    # ---------------------------------------------------------
    return {
        "decision": "reject",
        "next_rate": None,
        "round": negotiation_round,
        "reason": "negotiation_limit_reached",
    }
