from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from xml.sax.saxutils import escape as xml_escape
import os
import uuid
import json
import secrets

from fastapi import FastAPI, HTTPException, Form, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse
import httpx
from pydantic import BaseModel


class ChatRequest(BaseModel):
    sessionId: str
    message: str


class ChatReply(BaseModel):
    reply: str
    nextField: Optional[str] = None
    collected: Dict[str, str] = {}
    orderId: Optional[str] = None
    done: bool = False


class Offer(BaseModel):
    price: str
    eta: str  # planned delivery time
    driver: str
    accepted: Optional[bool] = None
    acceptedAt: Optional[datetime] = None


class Order(BaseModel):
    id: str
    createdAt: datetime
    data: Dict[str, str]
    createdBySession: Optional[str] = None
    offer: Optional[Offer] = None
    status: str = "Aktywne"
    publicToken: str


class AdminLogin(BaseModel):
    password: str


# In-memory stores (swap to Redis/DB later)
orders: Dict[str, Order] = {}
sessions: Dict[str, Dict] = {}
session_notifications: Dict[str, List[str]] = {}
acceptance_pending: Dict[str, str] = {}
admin_sessions: set[str] = set()

data_path = Path(__file__).parent / "data"
data_path.mkdir(exist_ok=True)
ORDERS_FILE = data_path / "orders.json"
static_path = Path(__file__).parent / "static"
static_path.mkdir(parents=True, exist_ok=True)

WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "qqq")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}

NEW_COMMANDS = {"n", "nowe", "nowa", "nowy"}
EDIT_COMMANDS = {"e", "edit", "edytuj", "edycja"}
LIST_COMMANDS = {"l", "lista", "list"}
RESET_COMMANDS = {"reset", "restart", "zacznij od nowa"}
YES_COMMANDS = {"t", "tak", "y", "yes"}
NO_COMMANDS = {"n", "nie", "no", "x"}
DELETE_COMMANDS = {"usun", "usuń", "delete"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def order_to_dict(order: Order) -> Dict:
    data = {
        "id": order.id,
        "createdAt": order.createdAt.isoformat(),
        "data": order.data,
        "createdBySession": order.createdBySession,
        "status": order.status,
        "publicToken": order.publicToken,
    }
    if order.offer:
        data["offer"] = {
            "price": order.offer.price,
            "eta": order.offer.eta,
            "driver": order.offer.driver,
            "accepted": order.offer.accepted,
            "acceptedAt": order.offer.acceptedAt.isoformat() if order.offer.acceptedAt else None,
        }
    return data


def dict_to_order(data: Dict) -> Order:
    offer_data = data.get("offer")
    offer_obj = None
    if offer_data:
        offer_obj = Offer(
            price=offer_data.get("price", ""),
            eta=offer_data.get("eta", ""),
            driver=offer_data.get("driver", ""),
            accepted=offer_data.get("accepted"),
            acceptedAt=parse_datetime(offer_data.get("acceptedAt")),
        )
    return Order(
        id=data["id"],
        createdAt=parse_datetime(data["createdAt"]) or utcnow(),
        data=data.get("data", {}),
        createdBySession=data.get("createdBySession"),
        offer=offer_obj,
        status=data.get("status", "Aktywne"),
        publicToken=data.get("publicToken") or str(uuid.uuid4()),
    )


def persist_store() -> None:
    payload = [order_to_dict(o) for o in orders.values()]
    tmp = ORDERS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp.replace(ORDERS_FILE)


def load_store() -> None:
    if not ORDERS_FILE.exists():
        return
    try:
        loaded = json.loads(ORDERS_FILE.read_text())
        for item in loaded:
            o = dict_to_order(item)
            orders[o.id] = o
    except Exception:
        # If file is corrupted, start empty but keep file for inspection
        pass


def reset_runtime_state() -> None:
    """Used by tests to isolate cases."""
    orders.clear()
    sessions.clear()
    session_notifications.clear()
    acceptance_pending.clear()
    admin_sessions.clear()


load_store()

FIELDS = [
    ("client_name", "Podaj nazwę zleceniodawcy."),
    ("pickup", "Podaj adres załadunku (ulica, miasto, kraj)."),
    ("delivery", "Podaj adres rozładunku (ulica, miasto, kraj)."),
    ("cargo", "Opisz ładunek (typ, waga, wymiary, palety/sztuki)."),
    ("pickup_time", "Podaj termin załadunku (data i godzina, strefa czasowa jeśli inna)."),
    ("contact", "Podaj kontakt do osoby odpowiedzialnej (telefon/email)."),
    ("requirements", "Czy są wymagania specjalne? (ADR/chłodnia/winda/ponadgabaryt)"),
]

FIELD_KEYS = {key: prompt for key, prompt in FIELDS}
FIELD_LABELS = {
    "client_name": "Zleceniodawca",
    "pickup": "Adres załadunku",
    "delivery": "Adres rozładunku",
    "cargo": "Ładunek",
    "pickup_time": "Termin załadunku",
    "contact": "Kontakt",
    "requirements": "Wymagania specjalne",
    "whatsapp": "WhatsApp",
}
FIELD_ALIASES = {
    "zleceniodawca": "client_name",
    "nazwa": "client_name",
    "klient": "client_name",
    "załadunek": "pickup",
    "zaladunek": "pickup",
    "adres załadunku": "pickup",
    "adres zaladunku": "pickup",
    "rozładunek": "delivery",
    "rozladunek": "delivery",
    "adres rozładunku": "delivery",
    "adres rozladunku": "delivery",
    "ładunek": "cargo",
    "ladunek": "cargo",
    "termin": "pickup_time",
    "termin załadunku": "pickup_time",
    "termin zaladunku": "pickup_time",
    "kontakt": "contact",
    "wymagania": "requirements",
    "wymagania specjalne": "requirements",
}


def field_label(key: str) -> str:
    return FIELD_LABELS.get(key, key.replace("_", " ").capitalize())


def field_options_text() -> str:
    return ", ".join(f"{field_label(key)} [{key}]" for key, _ in FIELDS)


def resolve_field_key(message: str) -> Optional[str]:
    msg = message.lower().strip()
    if msg in FIELD_KEYS:
        return msg
    if msg in FIELD_ALIASES:
        return FIELD_ALIASES[msg]
    for key, label in FIELD_LABELS.items():
        if key in FIELD_KEYS and msg == label.lower():
            return key
    return None


def reset_session(session_id: str) -> Dict:
    sessions[session_id] = {
        "mode": None,  # None | "new" | "edit_select_id" | "edit_choose_field" | "edit_new_value" | "done"
        "step": 0,
        "fields": {},
        "edit_order_id": None,
        "edit_field": None,
        "pending_accept_order": None,
        "whatsapp": None,
    }
    return sessions[session_id]


def format_summary(fields: Dict[str, str]) -> str:
    lines = [f"- {field_label(key)}: {value}" for key, value in fields.items()]
    return "\n".join(lines)


def initial_prompt() -> str:
    return (
        "Co chcesz zrobić? wpisz: 'nowe', 'edytuj' lub 'lista'. "
        "W dowolnej chwili możesz wpisać 'restart' i zacząć rozmowę od początku."
    )


def public_view_url(token: str, request: Optional[Request] = None) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/view/{token}"
    if request is not None:
        return str(request.base_url).rstrip("/") + f"/view/{token}"
    return f"/view/{token}"


def find_order_by_public_token(token: str) -> Optional[Order]:
    for order in orders.values():
        if order.publicToken == token:
            return order
    return None


def list_orders_by_client(client_query: str) -> str:
    query = client_query.lower()
    matches = []
    for oid, order in orders.items():
        client = order.data.get("client_name", "")
        if query in client.lower():
            pickup = order.data.get("pickup", "-")
            delivery = order.data.get("delivery", "-")
            offer_status = "wycena: tak" if order.offer else "wycena: nie"
            if order.offer:
                if order.offer.accepted is True:
                    acceptance_status = "akceptacja: tak"
                elif order.offer.accepted is False:
                    acceptance_status = "akceptacja: nie"
                else:
                    acceptance_status = "akceptacja: w trakcie"
            else:
                acceptance_status = "akceptacja: brak"
            matches.append(f"- {oid} | {client} | {pickup} -> {delivery} | {offer_status} | {acceptance_status}")
    if not matches:
        return "Brak zleceń dla podanego zleceniodawcy."
    return "Znalezione zlecenia:\n" + "\n".join(matches)


def enqueue_notification(session_id: Optional[str], message: str) -> None:
    if not session_id:
        return
    session_notifications.setdefault(session_id, []).append(message)


def send_whatsapp_cloud_message(to: str, body: str) -> None:
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return
    url = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    try:
        httpx.post(
            url,
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            json=payload,
            timeout=10,
        )
    except Exception:
        # Fail silently to avoid breaking webhook; consider logging in real env
        pass


def is_admin_authenticated(request: Request) -> bool:
    token = request.cookies.get("admin_session")
    return bool(token and token in admin_sessions)


def password_matches(candidate: str, expected: str) -> bool:
    candidate_bytes = candidate.encode("utf-8")
    expected_bytes = expected.encode("utf-8")
    if len(candidate_bytes) != len(expected_bytes):
        return False
    return secrets.compare_digest(candidate_bytes, expected_bytes)


def require_admin(request: Request) -> None:
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=401, detail="Admin authentication required")


def set_admin_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=60 * 60 * 12,
        path="/",
    )


app = FastAPI(title="Transport Chatbot API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/chat/message", response_model=ChatReply)
def chat_message(payload: ChatRequest, request: Request) -> ChatReply:
    return handle_chat_message(payload, request)


def handle_chat_message(payload: ChatRequest, request: Optional[Request] = None) -> ChatReply:
    session_id = payload.sessionId.strip()
    message = payload.message.strip()

    state = sessions.get(session_id) or reset_session(session_id)
    message_lower = message.lower()

    # If we already finished a flow, start fresh
    if state.get("mode") == "done":
        state = reset_session(session_id)

    # Allow manual reset
    if message_lower in RESET_COMMANDS:
        state = reset_session(session_id)
        return ChatReply(reply=f"Sesja wyzerowana. {initial_prompt()}", nextField="choice")

    # Pending offer acceptance flow override
    pending_order = acceptance_pending.get(session_id) or state.get("pending_accept_order")
    if pending_order:
        # Allow user to bypass acceptance prompt with top-level commands
        bypass_cmds = NEW_COMMANDS | EDIT_COMMANDS | LIST_COMMANDS
        if message_lower in bypass_cmds:
            acceptance_pending.pop(session_id, None)
            state = reset_session(session_id)
        else:
            state["mode"] = "offer_confirm"
            state["pending_accept_order"] = pending_order

    if state["mode"] == "offer_confirm":
        order = orders.get(state["pending_accept_order"])
        if not order or not order.offer:
            acceptance_pending.pop(session_id, None)
            state = reset_session(session_id)
            return ChatReply(reply="Oferta wygasła lub zlecenie nie istnieje. " + initial_prompt(), nextField="choice")
        if order.status.lower() == "anulowane":
            acceptance_pending.pop(session_id, None)
            state = reset_session(session_id)
            return ChatReply(reply="Zlecenie jest anulowane. " + initial_prompt(), nextField="choice")

        if message_lower in YES_COMMANDS:
            order.offer.accepted = True
            order.offer.acceptedAt = utcnow()
            orders[order.id] = order
            persist_store()
            acceptance_pending.pop(session_id, None)
            state["mode"] = "done"
            summary = format_summary(order.data)
            return ChatReply(
                reply=(
                    f"Oferta zaakceptowana. ID: {order.id}\n"
                    f"{summary}\n"
                    f"Oferta:\n- Cena: {order.offer.price}\n- Planowany termin dostawy: {order.offer.eta}\n- Kierowca: {order.offer.driver}"
                ),
                orderId=order.id,
                collected=order.data,
                done=True,
            )

        if message_lower in NO_COMMANDS:
            order.offer.accepted = False
            orders[order.id] = order
            persist_store()
            acceptance_pending.pop(session_id, None)
            state["mode"] = "done"
            return ChatReply(reply="Oferta odrzucona. Jeśli chcesz nową wycenę, napisz 'lista' lub 'edytuj'.", orderId=order.id)

        # ask again if unclear
        return ChatReply(reply="Czy akceptujesz ofertę? (tak/nie)", nextField="confirm_offer")

    # Ask for choice if no mode yet
    if state["mode"] is None:
        if not message or message_lower in {"start", "hej", "cześć", "czesc"}:
            return ChatReply(reply=initial_prompt(), nextField="choice")

        if message_lower in NEW_COMMANDS:
            state["mode"] = "new"
            state["step"] = 0
            prompt = FIELDS[state["step"]][1]
            return ChatReply(reply=prompt, nextField=FIELDS[state["step"]][0])

        if message_lower in EDIT_COMMANDS or "zmien" in message_lower or "edyt" in message_lower:
            state["mode"] = "edit_select_id"
            return ChatReply(reply="Podaj ID istniejącego zlecenia do edycji.", nextField="order_id")

        if message_lower in LIST_COMMANDS or "lista" in message_lower:
            state["mode"] = "list_client"
            return ChatReply(reply="Podaj nazwę zleceniodawcy, aby wyszukać jego zlecenia.", nextField="client_name")

        return ChatReply(
            reply=(
                "Użyj poprawnego polecenia, moge pomóc w obsłudze zlecenia transportowego, wybierz co chcesz zrobić? "
                "wpisz: 'nowe', 'edytuj' lub 'lista'. "
                "W dowolnej chwili możesz wpisać 'restart' i zacząć rozmowę od początku."
            ),
            nextField="choice",
        )

    # LIST FLOW: ask for client name and return matches
    if state["mode"] == "list_client":
        if not message:
            return ChatReply(reply="Podaj nazwę zleceniodawcy, aby wyszukać.", nextField="client_name")
        result = list_orders_by_client(message)
        state["mode"] = "done"
        return ChatReply(
            reply=result + "\nCo dalej? wpisz: 'nowe', 'edytuj' lub 'lista'.",
            nextField="choice",
        )

    # EDIT FLOW: ask for order id
    if state["mode"] == "edit_select_id":
        order = orders.get(message)
        if not order:
            return ChatReply(reply="Nie znalazłem zlecenia o tym ID. Podaj poprawne ID.", nextField="order_id")
        state["edit_order_id"] = message
        state["mode"] = "edit_choose_field"
        options = field_options_text()
        summary = format_summary(order.data)
        return ChatReply(
            reply=(
                f"Znalazłem zlecenie {message}:\n{summary}\n"
                f"Które pole chcesz zmienić? ({options})\n"
                "Możliwe jest też usunięcie zlecenia — wpisz 'usuń' w trybie edycji, aby to zrobić."
            ),
            nextField="field",
            collected=order.data,
            orderId=message,
        )

    # EDIT FLOW: choose field
    if state["mode"] == "edit_choose_field":
        field_key = message_lower.strip()
        if field_key in DELETE_COMMANDS:
            state["mode"] = "delete_confirm"
            return ChatReply(
                reply=f"Czy na pewno usunąć zlecenie {state['edit_order_id']}? (tak/nie)",
                nextField="confirm_delete",
                orderId=state["edit_order_id"],
            )
        resolved = resolve_field_key(field_key)
        if not resolved:
            options = field_options_text()
            return ChatReply(reply=f"Nie znam takiego pola. Wybierz jedno z: {options}", nextField="field")
        state["edit_field"] = resolved
        state["mode"] = "edit_new_value"
        return ChatReply(reply=f"Podaj nową wartość dla '{field_label(resolved)}':", nextField=resolved)

    if state["mode"] == "delete_confirm":
        order_id = state.get("edit_order_id")
        order = orders.get(order_id)
        if not order_id or not order:
            state = reset_session(session_id)
            return ChatReply(reply="Zlecenie nie istnieje. Zacznij od nowa.", nextField="choice")
        if message_lower in YES_COMMANDS:
            order.status = "Anulowane"
            for sid, oid in list(acceptance_pending.items()):
                if oid == order_id:
                    acceptance_pending.pop(sid, None)
            orders[order_id] = order
            persist_store()
            state["mode"] = "done"
            return ChatReply(
                reply=f"Zlecenie {order_id} oznaczone jako 'Anulowane'. Co dalej? wpisz: 'nowe', 'edytuj' lub 'lista'.",
                orderId=order_id,
                collected=order.data,
                done=True,
            )
        if message_lower in NO_COMMANDS:
            state["mode"] = "edit_choose_field"
            options = field_options_text()
            return ChatReply(
                reply=(
                    "Usunięcie anulowane. Które pole chcesz zmienić? "
                    f"({options}). Możesz też wpisać 'usuń' aby skasować zlecenie."
                ),
                nextField="field",
                orderId=order_id,
            )
        return ChatReply(
            reply="Potwierdź usunięcie: wpisz 'tak' lub 'nie'.",
            nextField="confirm_delete",
            orderId=order_id,
        )

    # EDIT FLOW: set new value
    if state["mode"] == "edit_new_value":
        order_id = state["edit_order_id"]
        field_key = state["edit_field"]
        order = orders.get(order_id)
        if not order:
            state = reset_session(session_id)
            return ChatReply(reply="Sesja wygasła, zacznij od nowa.", nextField="choice")
        order.data[field_key] = message
        orders[order_id] = order
        persist_store()
        state["mode"] = "done"
        summary = format_summary(order.data)
        return ChatReply(
            reply=f"Zaktualizowano zlecenie {order_id}.\nNowe dane:\n{summary}\nCzy chcesz coś jeszcze? (wpisz 'nowe' lub 'edytuj')",
            orderId=order_id,
            collected=order.data,
        )

    # NEW FLOW: Confirmation step
    if state["mode"] == "new" and state["step"] == len(FIELDS):
        if message_lower in YES_COMMANDS:
            if state.get("whatsapp") and "whatsapp" not in state["fields"]:
                state["fields"]["whatsapp"] = state["whatsapp"]
            order_id = str(uuid.uuid4())[:8]
            public_token = str(uuid.uuid4())
            orders[order_id] = Order(
                id=order_id,
                createdAt=utcnow(),
                data=state["fields"].copy(),
                createdBySession=session_id,
                status="Aktywne",
                publicToken=public_token,
            )
            persist_store()
            state["mode"] = "done"
            view_url = public_view_url(public_token, request)
            reply_text = (
                f"Zlecenie zapisane. ID: {order_id}\n"
                f"Link do podglądu: {view_url}"
            )
            return ChatReply(reply=reply_text, done=True, orderId=order_id, collected=state["fields"])
        if message_lower in NO_COMMANDS:
            state = reset_session(session_id)
            return ChatReply(reply=f"Odrzucono. {initial_prompt()}", nextField="choice")

        summary = format_summary(state["fields"])
        return ChatReply(
            reply=f"Potwierdź 'tak' lub 'nie'.\n{summary}",
            nextField="confirm",
            collected=state["fields"],
        )

    # NEW FLOW: Regular field collection
    if state["mode"] == "new":
        current_key, current_prompt = FIELDS[state["step"]]
        if message:
            state["fields"][current_key] = message
            state["step"] += 1

        if state["step"] < len(FIELDS):
            next_key, next_prompt = FIELDS[state["step"]]
            return ChatReply(reply=f"Dzięki. {next_prompt}", nextField=next_key, collected=state["fields"])

        # Move to confirmation
        summary = format_summary(state["fields"])
        return ChatReply(
            reply=f"Podsumowanie:\n{summary}\nPotwierdzasz? (tak/nie)",
            nextField="confirm",
            collected=state["fields"],
        )

    # Fallback: start over choice
    state = reset_session(session_id)
    return ChatReply(reply=initial_prompt(), nextField="choice")


@app.get("/orders/{order_id}/public-link")
def get_public_link(order_id: str, request: Request) -> Dict[str, str]:
    require_admin(request)
    order = orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "orderId": order.id,
        "publicToken": order.publicToken,
        "url": public_view_url(order.publicToken, request),
    }


@app.get("/orders/{order_id}", response_model=Order)
def get_order(order_id: str, request: Request) -> Order:
    require_admin(request)
    order = orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.get("/orders", response_model=Dict[str, Order])
def list_orders(request: Request) -> Dict[str, Order]:
    require_admin(request)
    return orders


@app.post("/orders/{order_id}/offer")
def set_offer(order_id: str, offer: Offer, request: Request):
    require_admin(request)
    order = orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status.lower() == "anulowane":
        raise HTTPException(status_code=400, detail="Order is cancelled")
    order.offer = offer
    summary = format_summary(order.data)
    offer_text = (
        "Twoje zlecenie zostało wycenione.\n"
        f"ID: {order_id}\n"
        f"{summary}\n"
        "Oferta:\n"
        f"- Cena: {offer.price}\n"
        f"- Planowany termin dostawy: {offer.eta}\n"
        f"- Kierowca: {offer.driver}\n"
        "Czy akceptujesz ofertę? (tak/nie)"
    )
    enqueue_notification(order.createdBySession, offer_text)
    if order.createdBySession:
        acceptance_pending[order.createdBySession] = order_id
        # Wyślij proaktywnie do WhatsApp (jeśli mamy token/phone_id)
        send_whatsapp_cloud_message(order.createdBySession, offer_text)
    orders[order_id] = order
    persist_store()
    return {"status": "ok"}


@app.get("/public/orders/{public_token}")
def get_public_order(public_token: str) -> Dict:
    order = find_order_by_public_token(public_token)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    payload = order_to_dict(order)
    payload.pop("createdBySession", None)
    return payload


@app.get("/view/{public_token}")
def public_view_page(public_token: str):
    public_file = static_path / "public.html"
    if not public_file.exists():
        raise HTTPException(status_code=404, detail="Public page not found")
    if not find_order_by_public_token(public_token):
        # Still serve the page so the frontend can show a friendly 404.
        return FileResponse(public_file)
    return FileResponse(public_file)


@app.get("/chat/notifications")
def get_notifications(sessionId: str) -> Dict[str, List[str]]:
    msgs = session_notifications.pop(sessionId, [])
    return {"messages": msgs}


@app.post("/admin/login")
def admin_login(payload: AdminLogin, response: Response) -> Dict[str, str]:
    if not password_matches(payload.password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Invalid password")
    token = secrets.token_urlsafe(32)
    admin_sessions.add(token)
    set_admin_cookie(response, token)
    return {"status": "ok"}


@app.get("/admin/session")
def admin_session(request: Request) -> Dict[str, bool]:
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=401, detail="Admin authentication required")
    return {"authenticated": True}


@app.post("/admin/logout")
def admin_logout(request: Request, response: Response) -> Dict[str, str]:
    token = request.cookies.get("admin_session")
    if token:
        admin_sessions.discard(token)
    response.delete_cookie("admin_session", path="/")
    return {"status": "ok"}


@app.get("/webhook/whatsapp/meta")
def whatsapp_meta_verify(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
) -> PlainTextResponse:
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge or "")
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook/whatsapp/meta")
async def whatsapp_meta_webhook(payload: Dict) -> Dict[str, str]:
    entries = payload.get("entry", [])
    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            for msg in messages:
                if msg.get("type") != "text":
                    continue
                from_id = msg.get("from")
                text = msg.get("text", {}).get("body", "")
                if not from_id or not text:
                    continue
                st = sessions.get(from_id) or reset_session(from_id)
                st["whatsapp"] = from_id
                sessions[from_id] = st
                reply = handle_chat_message(ChatRequest(sessionId=from_id, message=text))
                send_whatsapp_cloud_message(from_id, reply.reply)
    return {"status": "ok"}


@app.post("/webhook/whatsapp")
def whatsapp_webhook(
    Body: str = Form(...),
    WaId: Optional[str] = Form(None),
    From: Optional[str] = Form(None),
) -> PlainTextResponse:
    """
    Simple Twilio WhatsApp webhook.
    Uses WaId/From as sessionId, routes message through chat logic, returns TwiML.
    """
    session_id = (WaId or From or "").strip() or str(uuid.uuid4())
    st = sessions.get(session_id) or reset_session(session_id)
    st["whatsapp"] = session_id
    sessions[session_id] = st
    reply = handle_chat_message(ChatRequest(sessionId=session_id, message=Body))
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Message>"
        f"{xml_escape(reply.reply)}"
        "</Message></Response>"
    )
    return PlainTextResponse(content=twiml, media_type="application/xml")


@app.get("/admin")
def admin_page():
    """Serve a simple admin view listing all orders."""
    admin_file = static_path / "admin.html"
    if not admin_file.exists():
        raise HTTPException(status_code=404, detail="Admin page not found")
    return FileResponse(admin_file)


app.mount("/", StaticFiles(directory=static_path, html=True), name="static")


def create_app() -> FastAPI:
    """Allow reuse in ASGI servers/tests."""
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
