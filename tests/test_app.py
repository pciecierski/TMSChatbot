import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))


@pytest.fixture()
def client(tmp_path):
    import main as app_module

    app_module.ORDERS_FILE = tmp_path / "orders.json"
    app_module.reset_runtime_state()
    with TestClient(app_module.app) as test_client:
        yield test_client
    app_module.reset_runtime_state()


def chat(client: TestClient, session_id: str, message: str):
    response = client.post("/chat/message", json={"sessionId": session_id, "message": message})
    assert response.status_code == 200
    return response.json()


def create_order(client: TestClient, session_id: str = "sess-1", client_name: str = "ACME") -> dict:
    chat(client, session_id, "nowe")
    chat(client, session_id, client_name)
    chat(client, session_id, "Warszawa")
    chat(client, session_id, "Berlin")
    chat(client, session_id, "palety")
    chat(client, session_id, "2026-08-20 10:00")
    chat(client, session_id, "jan@acme.pl")
    summary = chat(client, session_id, "brak")
    assert "Podsumowanie" in summary["reply"]
    assert "Zleceniodawca" in summary["reply"]
    done = chat(client, session_id, "tak")
    assert done["done"] is True
    assert done["orderId"]
    assert "/view/" in done["reply"]
    return done


def admin_login(client: TestClient, password: str = "qqq"):
    response = client.post("/admin/login", json={"password": password})
    return response


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_nie_does_not_start_new_order(client: TestClient):
    reply = chat(client, "s1", "nie")
    assert reply["nextField"] == "choice"
    assert "nowe" in reply["reply"]


def test_create_order_and_public_view(client: TestClient):
    created = create_order(client)
    order_id = created["orderId"]

    unauth = client.get("/orders")
    assert unauth.status_code == 401

    login = admin_login(client)
    assert login.status_code == 200

    listed = client.get("/orders")
    assert listed.status_code == 200
    payload = listed.json()
    assert order_id in payload
    token = payload[order_id]["publicToken"]

    public = client.get(f"/public/orders/{token}")
    assert public.status_code == 200
    public_data = public.json()
    assert public_data["id"] == order_id
    assert "createdBySession" not in public_data
    assert public_data["data"]["client_name"] == "ACME"

    view = client.get(f"/view/{token}")
    assert view.status_code == 200
    assert "text/html" in view.headers["content-type"]

    link = client.get(f"/orders/{order_id}/public-link")
    assert link.status_code == 200
    assert token in link.json()["url"]


def test_edit_by_polish_label(client: TestClient):
    created = create_order(client, "edit-sess")
    order_id = created["orderId"]

    chat(client, "edit-sess", "edytuj")
    found = chat(client, "edit-sess", order_id)
    assert "Zleceniodawca" in found["reply"]
    chat(client, "edit-sess", "ładunek")
    updated = chat(client, "edit-sess", "stal")
    assert "Zaktualizowano" in updated["reply"]
    assert "stal" in updated["reply"]


def test_offer_requires_admin_and_can_be_accepted(client: TestClient):
    created = create_order(client, "offer-sess")
    order_id = created["orderId"]

    denied = client.post(
        f"/orders/{order_id}/offer",
        json={"price": "1000 PLN", "eta": "2026-08-21", "driver": "Anna"},
    )
    assert denied.status_code == 401

    assert admin_login(client).status_code == 200
    offered = client.post(
        f"/orders/{order_id}/offer",
        json={"price": "1000 PLN", "eta": "2026-08-21", "driver": "Anna"},
    )
    assert offered.status_code == 200

    notes = client.get("/chat/notifications", params={"sessionId": "offer-sess"})
    assert notes.status_code == 200
    assert notes.json()["messages"]

    accepted = chat(client, "offer-sess", "tak")
    assert "zaakceptowana" in accepted["reply"].lower()


def test_admin_login_rejects_wrong_password(client: TestClient):
    response = admin_login(client, "wrong")
    assert response.status_code == 401
    assert client.get("/admin/session").status_code == 401


def test_twiml_escapes_xml(client: TestClient):
    first = client.post("/webhook/whatsapp", data={"Body": "nowe", "WaId": "48111"})
    assert first.status_code == 200
    response = client.post(
        "/webhook/whatsapp",
        data={"Body": "<script>alert(1)</script>", "WaId": "48111"},
    )
    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
