from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import uuid
import json
import os

from fastapi import FastAPI, HTTPException, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.responses import PlainTextResponse
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


# In-memory stores (swap to Redis/DB later)
orders: Dict[str, Order] = {}
sessions: Dict[str, Dict] = {}
session_notifications: Dict[str, List[str]] = {}
acceptance_pending: Dict[str, str] = {}

data_path = Path(__file__).parent / "data"
data_path.mkdir(exist_ok=True)
ORDERS_FILE = data_path / "orders.json"


def order_to_dict(order: Order) -> Dict:
    data = {
        "id": order.id,
        "createdAt": order.createdAt.isoformat(),
        "data": order.data,
        "createdBySession": order.createdBySession,
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
            acceptedAt=datetime.fromisoformat(offer_data["acceptedAt"]) if offer_data.get("acceptedAt") else None,
        )
    return Order(
        id=data["id"],
        createdAt=datetime.fromisoformat(data["createdAt"]),
        data=data.get("data", {}),
        createdBySession=data.get("createdBySession"),
        offer=offer_obj,
    )


def persist_store() -> None:
    payload = [order_to_dict(o) for o in orders.values()]
    ORDERS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


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


load_store()

WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

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
    lines = [f"- {label.capitalize().replace('_', ' ')}: {value}" for label, value in fields.items()]
    return "\n".join(lines)


def initial_prompt() -> str:
    return "Co chcesz zrobić? wpisz: 'nowe', 'edytuj' lub 'lista'."


def list_orders_by_client(client_query: str) -> str:
    query = client_query.lower()
    matches = []
    for oid, order in orders.items():
        client = order.data.get("client_name", "")
        if query in client.lower():
            pickup = order.data.get("pickup", "-")
            delivery = order.data.get("delivery", "-")
            matches.append(f"- {oid} | {client} | {pickup} -> {delivery}")
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


app = FastAPI(title="Transport Chatbot API", version="0.1.0")

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
def chat_message(payload: ChatRequest) -> ChatReply:
    session_id = payload.sessionId.strip()
    message = payload.message.strip()

    state = sessions.get(session_id) or reset_session(session_id)
    message_lower = message.lower()

    # If we already finished a flow, start fresh
    if state.get("mode") == "done":
        state = reset_session(session_id)

    # Allow manual reset
    if message_lower in {"reset", "restart", "zacznij od nowa"}:
        state = reset_session(session_id)
        return ChatReply(reply=f"Sesja wyzerowana. {initial_prompt()}", nextField="choice")

    # Pending offer acceptance flow override
    pending_order = acceptance_pending.get(session_id) or state.get("pending_accept_order")
    if pending_order:
        state["mode"] = "offer_confirm"
        state["pending_accept_order"] = pending_order

    if state["mode"] == "offer_confirm":
        order = orders.get(state["pending_accept_order"])
        if not order or not order.offer:
            acceptance_pending.pop(session_id, None)
            state = reset_session(session_id)
            return ChatReply(reply="Oferta wygasła lub zlecenie nie istnieje. " + initial_prompt(), nextField="choice")

        if message_lower.startswith(("t", "y")):
            order.offer.accepted = True
            order.offer.acceptedAt = datetime.utcnow()
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

        if message_lower.startswith(("n", "x")):
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

        if message_lower.startswith("n"):
            state["mode"] = "new"
            state["step"] = 0
            prompt = FIELDS[state["step"]][1]
            return ChatReply(reply=prompt, nextField=FIELDS[state["step"]][0])

        if message_lower.startswith("e") or "zmien" in message_lower or "edyt" in message_lower:
            state["mode"] = "edit_select_id"
            return ChatReply(reply="Podaj ID istniejącego zlecenia do edycji.", nextField="order_id")

        if message_lower.startswith("l") or "lista" in message_lower or "list" in message_lower:
            state["mode"] = "list_client"
            return ChatReply(reply="Podaj nazwę zleceniodawcy, aby wyszukać jego zlecenia.", nextField="client_name")

        return ChatReply(reply=f"Nie rozumiem. {initial_prompt()}", nextField="choice")

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
        options = ", ".join(FIELD_KEYS.keys())
        summary = format_summary(order.data)
        return ChatReply(
            reply=f"Znalazłem zlecenie {message}:\n{summary}\nKtóre pole chcesz zmienić? ({options})",
            nextField="field",
            collected=order.data,
            orderId=message,
        )

    # EDIT FLOW: choose field
    if state["mode"] == "edit_choose_field":
        field_key = message_lower.strip()
        if field_key not in FIELD_KEYS:
            options = ", ".join(FIELD_KEYS.keys())
            return ChatReply(reply=f"Nie znam takiego pola. Wybierz jedno z: {options}", nextField="field")
        state["edit_field"] = field_key
        state["mode"] = "edit_new_value"
        return ChatReply(reply=f"Podaj nową wartość dla '{field_key}':", nextField=field_key)

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
        if message_lower.startswith(("t", "y")):
            if state.get("whatsapp") and "whatsapp" not in state["fields"]:
                state["fields"]["whatsapp"] = state["whatsapp"]
            order_id = str(uuid.uuid4())[:8]
            orders[order_id] = Order(
                id=order_id,
                createdAt=datetime.utcnow(),
                data=state["fields"].copy(),
                createdBySession=session_id,
            )
            persist_store()
            state["mode"] = "done"
            reply_text = f"Zlecenie zapisane. ID: {order_id}"
            return ChatReply(reply=reply_text, done=True, orderId=order_id, collected=state["fields"])
        if message_lower.startswith(("n", "x")):
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


@app.get("/orders/{order_id}", response_model=Order)
def get_order(order_id: str) -> Order:
    order = orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.get("/orders", response_model=Dict[str, Order])
def list_orders() -> Dict[str, Order]:
    return orders


@app.post("/orders/{order_id}/offer")
def set_offer(order_id: str, offer: Offer):
    order = orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
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


@app.get("/chat/notifications")
def get_notifications(sessionId: str) -> Dict[str, List[str]]:
    msgs = session_notifications.pop(sessionId, [])
    return {"messages": msgs}


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
                reply = chat_message(ChatRequest(sessionId=from_id, message=text))
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
    reply = chat_message(ChatRequest(sessionId=session_id, message=Body))
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Message>"
        f"{reply.reply}"
        "</Message></Response>"
    )
    return PlainTextResponse(content=twiml, media_type="application/xml")


static_path = Path(__file__).parent / "static"
static_path.mkdir(parents=True, exist_ok=True)


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
