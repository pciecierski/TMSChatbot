import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))


@pytest.fixture()
def client(tmp_path):
    import conversations as conv_mod
    import main as app_module

    app_module.ORDERS_FILE = tmp_path / "orders.json"
    conv_mod.CONVERSATIONS_FILE = tmp_path / "conversations.json"
    app_module.YARD_FILE = tmp_path / "yard_requests.json"
    app_module.VISITS_FILE = tmp_path / "visits.json"
    app_module.reset_runtime_state()
    with TestClient(app_module.app) as test_client:
        yield test_client
    app_module.reset_runtime_state()


def chat(client: TestClient, session_id: str, message: str):
    response = client.post("/chat/message", json={"sessionId": session_id, "message": message})
    assert response.status_code == 200
    return response.json()


def create_order(client: TestClient, session_id: str = "sess-1", client_name: str = "ACME") -> dict:
    chat(client, session_id, "nie")
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


def test_pages_use_mobile_viewport(client: TestClient):
    home = client.get("/")
    assert home.status_code == 200
    assert 'name="viewport"' in home.text
    assert "width=device-width" in home.text
    assert "viewport-fit=cover" in home.text
    assert "styles.css?v=20260819f" in home.text

    styles = client.get("/styles.css?v=20260819f")
    assert styles.status_code == 200
    assert "overflow-x: hidden" in styles.text
    assert "min-width: 0" in styles.text


def test_greeting_asks_if_on_site(client: TestClient):
    reply = chat(client, "boot", "start")
    assert "placu" in reply["reply"].lower()
    labels = [btn["label"] for btn in reply["buttons"]]
    assert labels == ["Tak", "Nie"]


def test_off_site_shows_spedytor_menu(client: TestClient):
    reply = chat(client, "s1", "nie")
    assert reply["nextField"] == "choice"
    assert "Krok 1/" not in reply["reply"]
    labels = [btn["label"] for btn in reply["buttons"]]
    assert "Nowe zlecenie" in labels
    assert "Jestem na terenie parku" not in labels


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
    create_order(client, "edit-sess")
    listed = chat(client, "edit-sess", "edytuj")
    assert "1." in listed["reply"]
    assert "ACME" in listed["reply"]
    card = chat(client, "edit-sess", "1")
    assert "Zleceniodawca" in card["reply"]
    chat(client, "edit-sess", "edytuj")
    chat(client, "edit-sess", "ładunek")
    updated = chat(client, "edit-sess", "stal")
    assert "Zaktualizowano" in updated["reply"]
    assert "stal" in updated["reply"]


def test_list_shows_orders_and_events(client: TestClient):
    create_order(client, "list-sess", "ACME")
    listed = chat(client, "list-sess", "lista")
    assert "Twoje zlecenia:" in listed["reply"]
    assert "Ostatnie zdarzenia:" in listed["reply"]
    assert "czeka na wycenę" in listed["reply"]
    assert any(btn["value"] == "1" for btn in listed["buttons"])


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
    steps = [
        "nie",
        "nowe",
        "<script>alert(1)</script>",
        "Warszawa",
        "Berlin",
        "palety",
        "2026-08-20 10:00",
        "jan@acme.pl",
        "brak",
    ]
    last = None
    for body in steps:
        last = client.post("/webhook/whatsapp", data={"Body": body, "WaId": "48111"})
        assert last.status_code == 200
    assert last is not None
    assert "<script>" not in last.text
    assert "&lt;script&gt;" in last.text


def test_conversations_are_saved_and_archived(client: TestClient):
    visitor = "visitor-1"
    created = client.post("/chat/conversations", json={"visitorId": visitor})
    assert created.status_code == 200
    first_id = created.json()["id"]

    chat_res = client.post(
        "/chat/message",
        json={"visitorId": visitor, "conversationId": first_id, "sessionId": first_id, "message": "nowe"},
    )
    assert chat_res.status_code == 200
    assert chat_res.json()["conversationId"] == first_id

    history = client.get(f"/chat/conversations/{first_id}", params={"visitorId": visitor})
    assert history.status_code == 200
    messages = history.json()["messages"]
    assert any(msg["role"] == "user" and msg["text"] == "nowe" for msg in messages)
    assert any(msg["role"] == "bot" for msg in messages)

    second = client.post("/chat/conversations", json={"visitorId": visitor})
    assert second.status_code == 200
    second_id = second.json()["id"]
    assert second_id != first_id

    listed = client.get("/chat/conversations", params={"visitorId": visitor})
    items = listed.json()["conversations"]
    by_id = {item["id"]: item for item in items}
    assert by_id[first_id]["archived"] is True
    assert by_id[second_id]["archived"] is False

    reopened = client.post(f"/chat/conversations/{first_id}/reopen", json={"visitorId": visitor})
    assert reopened.status_code == 200
    listed = client.get("/chat/conversations", params={"visitorId": visitor})
    by_id = {item["id"]: item for item in listed.json()["conversations"]}
    assert by_id[first_id]["archived"] is False
    assert by_id[second_id]["archived"] is True


def test_yard_request_requires_onsite(client: TestClient):
    first = chat(client, "drv-1", "start")
    assert "placu" in first["reply"].lower()
    denied_location = chat(client, "drv-1", "nie")
    assert "Nowe zlecenie" in [btn["label"] for btn in denied_location["buttons"]]
    denied = chat(client, "drv-1", "park")
    assert "tylko od kierowców" in denied["reply"]
    listed = client.get("/yard-requests")
    assert listed.status_code == 401


def test_yard_pause_request_flow(client: TestClient):
    chat(client, "drv-2", "tak")
    chat(client, "drv-2", "Jan Kowalski")
    menu = chat(client, "drv-2", "WZ 1234A / WZ 5678B")
    labels = [btn["label"] for btn in menu["buttons"]]
    assert "Sprawdzenie postępów wizyty" in labels
    assert "Pauza / dodatkowy postój" in labels
    kind = chat(client, "drv-2", "pauza")
    assert "postój" in kind["reply"].lower() or "postoju" in kind["reply"].lower()
    chat(client, "drv-2", "45 min")
    done = chat(client, "drv-2", "tak")
    assert done["done"] is True
    assert "Zgłoszenie przyjęte" in done["reply"]
    after = [btn["label"] for btn in done["buttons"]]
    assert "Sprawdzenie postępów wizyty" in after

    assert admin_login(client).status_code == 200
    listed = client.get("/yard-requests")
    assert listed.status_code == 200
    items = list(listed.json().values())
    assert len(items) == 1
    assert items[0]["kind"] == "pauza"
    assert items[0]["data"]["driver_name"] == "Jan Kowalski"
    req_id = items[0]["id"]
    updated = client.post(f"/yard-requests/{req_id}/status", json={"status": "Przyjęte"})
    assert updated.status_code == 200
    assert client.get("/yard-requests").json()[req_id]["status"] == "Przyjęte"


def test_on_site_stays_in_dispatcher_menu(client: TestClient):
    chat(client, "onsite-1", "tak")
    chat(client, "onsite-1", "Jan Kowalski")
    menu = chat(client, "onsite-1", "WZ 1234A / WZ 5678B")
    labels = [btn["label"] for btn in menu["buttons"]]
    assert "Sprawdzenie postępów wizyty" in labels
    assert "Nowe zlecenie" not in labels
    blocked = chat(client, "onsite-1", "nowe")
    assert "Krok 1/" not in blocked["reply"]
    assert "dyspozytor" in blocked["reply"].lower()


def test_visit_progress_lookup(client: TestClient):
    chat(client, "vis-ok", "tak")
    chat(client, "vis-ok", "Jan Kowalski")
    chat(client, "vis-ok", "WZ 1234A / WZ 5678B")
    confirm = chat(client, "vis-ok", "postep")
    assert "Jan Kowalski" in confirm["reply"]
    result = chat(client, "vis-ok", "tak")
    assert "przypisany dok i przekazane do realizacji" in result["reply"]
    assert "wizyta rozpoczęta" in result["reply"]
    assert "przygotowane dokumenty" in result["reply"]
    assert "aktualny etap" in result["reply"]


def test_visit_progress_not_found(client: TestClient):
    chat(client, "vis-miss", "tak")
    chat(client, "vis-miss", "Nieznany Kierowca")
    chat(client, "vis-miss", "XX 0000")
    chat(client, "vis-miss", "postep")
    result = chat(client, "vis-miss", "tak")
    assert "nie znalazłem wizyty" in result["reply"].lower()


def test_admin_can_advance_visit_stage(client: TestClient):
    denied = client.get("/visits")
    assert denied.status_code == 401
    assert admin_login(client).status_code == 200
    listed = client.get("/visits")
    assert listed.status_code == 200
    assert "vis-1" in listed.json()
    assert listed.json()["vis-1"]["stage"] == "dok"
    advanced = client.post("/visits/vis-1/stage", json={"advance": True})
    assert advanced.status_code == 200
    assert advanced.json()["stage"] == "zaladunek"
    assert client.get("/visits").json()["vis-1"]["stage"] == "zaladunek"
