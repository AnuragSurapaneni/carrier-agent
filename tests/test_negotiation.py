from fastapi.testclient import TestClient
from app.main import API_KEY
from app.main import app, NEGOTIATION_STATE, NEGOTIATION_LIMITS

client = TestClient(app)



def headers():
    return {"X-API-Key": API_KEY}


def setup_function():
    NEGOTIATION_STATE.clear()

    # Use a known test load.
    NEGOTIATION_LIMITS["TEST-LOAD"] = 600


def test_first_offer_above_limit_returns_counter():
    response = client.post(
        "/evaluate-offer",
        headers=headers(),
        json={
            "load_id": "TEST-LOAD",
            "carrier_rate": "800",
            "round": "1",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["decision"] == "counter"
    assert data["round"] == 1
    assert data["next_rate"] < 600


def test_acceptable_offer_is_accepted():
    response = client.post(
        "/evaluate-offer",
        headers=headers(),
        json={
            "load_id": "TEST-LOAD",
            "carrier_rate": "550",
            "round": "1",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["decision"] == "accept"
    assert data["next_rate"] == 550

    state = NEGOTIATION_STATE["TEST-LOAD"]

    assert state["accepted"] is True
    assert state["agreed_rate"] == 550


def test_fourth_negotiation_round_is_rejected():
    # Round 1
    client.post(
        "/evaluate-offer",
        headers=headers(),
        json={
            "load_id": "TEST-LOAD",
            "carrier_rate": "800",
            "round": "1",
        },
    )

    # Round 2
    client.post(
        "/evaluate-offer",
        headers=headers(),
        json={
            "load_id": "TEST-LOAD",
            "carrier_rate": "800",
            "round": "2",
        },
    )

    # Round 3
    response = client.post(
        "/evaluate-offer",
        headers=headers(),
        json={
            "load_id": "TEST-LOAD",
            "carrier_rate": "800",
            "round": "3",
        },
    )

    assert response.json()["decision"] in {
        "reject",
        "counter",
        "final_counter",
    }

    # Round 4 must not be allowed.
    response = client.post(
        "/evaluate-offer",
        headers=headers(),
        json={
            "load_id": "TEST-LOAD",
            "carrier_rate": "800",
            "round": "1",
        },
    )

    data = response.json()

    assert data["decision"] == "reject"
    assert data["reason"] == "negotiation_limit_reached"


def test_agent_cannot_reset_round_by_sending_round_one():
    for _ in range(3):
        response = client.post(
            "/evaluate-offer",
            headers=headers(),
            json={
                "load_id": "TEST-LOAD",
                "carrier_rate": "800",
                "round": "1",
            },
        )

    assert NEGOTIATION_STATE["TEST-LOAD"]["round"] == 3

    response = client.post(
        "/evaluate-offer",
        headers=headers(),
        json={
            "load_id": "TEST-LOAD",
            "carrier_rate": "800",
            "round": "1",
        },
    )

    assert response.json()["decision"] == "reject"
    assert response.json()["reason"] == "negotiation_limit_reached"
