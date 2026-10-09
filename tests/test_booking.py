from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from app.main import API_KEY
from app.main import app, OTP_STORE, BOOKINGS, NEGOTIATION_STATE

client = TestClient(app)



def headers():
    return {"X-API-Key": API_KEY}


def setup_function():
    OTP_STORE.clear()
    BOOKINGS.clear()
    NEGOTIATION_STATE.clear()


def verify_carrier(run_id="test-run", mc_number="9999901"):
    OTP_STORE[f"{run_id}:{mc_number}"] = {
        "verified": True,
        "expires_at": datetime.utcnow() + timedelta(minutes=5),
    }


def test_booking_rejects_missing_load_id():
    run_id = "test-run"
    mc = "9999901"

    verify_carrier(run_id, mc)

    response = client.post(
        "/book",
        headers=headers(),
        json={
            "run_id": run_id,
            "load_id": "",
            "mc_number": mc,
            "agreed_rate": "650",
        },
    )

    assert response.status_code == 200
    assert response.json()["booked"] is False
    assert response.json()["reason"] == "missing_load_id"


def test_booking_rejects_zero_rate():
    run_id = "test-run"
    mc = "9999901"

    verify_carrier(run_id, mc)

    response = client.post(
        "/book",
        headers=headers(),
        json={
            "run_id": run_id,
            "load_id": "LD00880",
            "mc_number": mc,
            "agreed_rate": "0",
        },
    )

    assert response.status_code == 200
    assert response.json()["booked"] is False
    assert response.json()["reason"] == "invalid_rate"


def test_booking_rejects_without_negotiation():
    run_id = "test-run"
    mc = "9999901"

    verify_carrier(run_id, mc)

    response = client.post(
        "/book",
        headers=headers(),
        json={
            "run_id": run_id,
            "load_id": "LD00880",
            "mc_number": mc,
            "agreed_rate": "650",
        },
    )

    assert response.status_code == 200
    assert response.json()["booked"] is False
    assert response.json()["reason"] == "no_negotiation"


def test_booking_rejects_when_rate_was_not_accepted():
    run_id = "test-run"
    mc = "9999901"
    load_id = "LD00880"

    verify_carrier(run_id, mc)

    NEGOTIATION_STATE[load_id] = {
        "round": 1,
        "accepted": False,
        "agreed_rate": None,
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

    assert response.status_code == 200
    assert response.json()["booked"] is False
    assert response.json()["reason"] == "carrier_rate_not_accepted"


def test_booking_rejects_rate_different_from_accepted_rate():
    run_id = "test-run"
    mc = "9999901"
    load_id = "LD00880"

    verify_carrier(run_id, mc)

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
            "agreed_rate": "700",
        },
    )

    assert response.status_code == 200
    assert response.json()["booked"] is False
    assert response.json()["reason"] == "rate_not_matching_accepted_rate"


def test_successful_test_booking():
    run_id = "test-run"
    mc = "9999901"
    load_id = "LD00880"

    verify_carrier(run_id, mc)

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

    assert response.status_code == 200

    data = response.json()

    assert data["booked"] is True
    assert data["booking_id"] == f"TEST-{load_id}-BOOKED"


def test_booking_rejects_expired_otp():
    run_id = "test-run"
    mc = "9999901"
    load_id = "LD00880"

    # Create an expired OTP (verified true but expired)
    from datetime import datetime, timedelta
    expired_time = datetime.utcnow() - timedelta(hours=3)  # expired
    OTP_STORE[f"{run_id}:{mc}"] = {
        "verified": True,
        "expires_at": expired_time,
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

    assert response.status_code == 200
    assert response.json()["booked"] is False
    assert response.json()["reason"] == "otp_expired"


def test_booking_rejects_invalid_accepted_rate():
    run_id = "test-run"
    mc = "9999901"
    load_id = "LD00880"

    # Create OTP
    OTP_STORE[f"{run_id}:{mc}"] = {
        "verified": True,
        "expires_at": datetime.utcnow() + timedelta(minutes=5),
    }

    # Negotiation with non-numeric agreed_rate
    NEGOTIATION_STATE[load_id] = {
        "round": 1,
        "accepted": True,
        "agreed_rate": "invalid",
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

    assert response.status_code == 200
    assert response.json()["booked"] is False
    assert response.json()["reason"] == "invalid_accepted_rate"
