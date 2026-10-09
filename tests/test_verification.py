from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from app.main import API_KEY
from app.main import app, OTP_STORE, NEGOTIATION_STATE, BOOKINGS

client = TestClient(app)



def headers():
    return {"X-API-Key": API_KEY}


def setup_function():
    OTP_STORE.clear()
    NEGOTIATION_STATE.clear()
    BOOKINGS.clear()


def test_unverified_carrier_cannot_book():
    response = client.post(
        "/book",
        headers=headers(),
        json={
            "run_id": "test-run",
            "load_id": "LD00880",
            "mc_number": "9999901",
            "agreed_rate": "650",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["booked"] is False
    assert data["reason"] == "carrier_not_verified"


def test_verified_carrier_can_reach_booking_validation():
    run_id = "test-run"
    mc = "9999901"
    load_id = "LD00880"

    OTP_STORE[f"{run_id}:{mc}"] = {
        "verified": True,
        "expires_at": datetime.utcnow() + timedelta(minutes=5),
    }

    NEGOTIATION_STATE[load_id] = {
        "round": 1,
        "accepted": True,
        "agreed_rate": 650,
    }

    response = client.post(
        "/book",
        headers=headers(),
        json={
            "run_id": run_id,
            "load_id": load_id,
            "mc_number": mc,
            "agreed_rate": "650",
        },
    )

    data = response.json()

    assert data["booked"] is True
