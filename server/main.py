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

from conversations import (
    append_message,
    conversation_ids_for_visitor,
    conversations as conversation_store,
    create_conversation,
    ensure_conversation,
    get_conversation,
    list_for_visitor,
    maybe_update_title,
    reopen_conversation,
    reset_conversations,
    save_agent_state,
    serialize_conversation,
)


class ChatRequest(BaseModel):
    sessionId: str = ""
    visitorId: Optional[str] = None
    conversationId: Optional[str] = None
    message: str
    userDisplay: Optional[str] = None


class ChatButton(BaseModel):
    label: str
    value: str


class ChatReply(BaseModel):
    reply: str
    nextField: Optional[str] = None
    collected: Dict[str, str] = {}
    orderId: Optional[str] = None
    done: bool = False
    buttons: List[ChatButton] = []
    conversationId: Optional[str] = None


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

NEW_COMMANDS = {"n", "nowe", "nowa", "nowy", "nowe zlecenie"}
EDIT_COMMANDS = {"e", "edit", "edytuj", "edycja", "zmień zlecenie", "zmien zlecenie", "popraw"}
LIST_COMMANDS = {
    "l",
    "lista",
    "list",
    "moje zlecenia",
    "zlecenia",
    "status",
    "zdarzenia",
    "moje zdarzenia",
}
RESET_COMMANDS = {"reset", "restart", "zacznij od nowa"}
START_COMMANDS = {"start", "hej", "cześć", "czesc", "menu", "pomoc"}
YES_COMMANDS = {"t", "tak", "y", "yes"}
NO_COMMANDS = {"n", "nie", "no", "x"}
DELETE_COMMANDS = {"usun", "usuń", "delete", "anuluj zlecenie"}
BACK_COMMANDS = {"powrót", "powrot", "wstecz", "menu"}


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
    reset_conversations()


def restore_sessions_from_conversations() -> None:
    for conv in conversation_store.values():
        state = conv.get("agentState")
        if state:
            sessions[conv["id"]] = state


load_store()
restore_sessions_from_conversations()

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
        "mode": None,  # None | "new" | "select_order" | "edit_choose_field" | "edit_new_value" | "done"
        "step": 0,
        "fields": {},
        "edit_order_id": None,
        "edit_field": None,
        "pending_accept_order": None,
        "whatsapp": None,
        "listed_ids": [],
    }
    return sessions[session_id]


def format_summary(fields: Dict[str, str]) -> str:
    lines = [f"- {field_label(key)}: {value}" for key, value in fields.items()]
    return "\n".join(lines)


def initial_prompt() -> str:
    return (
        "Cześć. Jestem automatycznym agentem — mogę pełnić rolę spedytora, dyspozytora, "
        "administratora albo biura magazynu.\n\n"
        "Wyjaśnię, w czym mogę pomóc, a potem poprowadzę Cię krok po kroku. Umiem:\n"
        "- przyjąć nowe zlecenie transportowe,\n"
        "- pokazać Twoje zlecenia i zdarzenia (wycena, oferta, akceptacja, anulowanie),\n"
        "- poprawić dane albo anulować zlecenie,\n"
        "- przyjąć decyzję o ofercie, gdy przyjdzie wycena.\n\n"
        "Wybierz akcję albo napisz, czego potrzebujesz."
    )


def main_action_buttons() -> List[ChatButton]:
    return [
        ChatButton(label="Nowe zlecenie", value="nowe"),
        ChatButton(label="Moje zlecenia", value="lista"),
        ChatButton(label="Zmień zlecenie", value="edytuj"),
        ChatButton(label="Restart", value="restart"),
    ]


def yes_no_buttons() -> List[ChatButton]:
    return [
        ChatButton(label="Tak", value="tak"),
        ChatButton(label="Nie", value="nie"),
    ]


def field_buttons() -> List[ChatButton]:
    buttons = [ChatButton(label=field_label(key), value=key) for key, _ in FIELDS]
    buttons.append(ChatButton(label="Anuluj zlecenie", value="usuń"))
    buttons.append(ChatButton(label="Powrót", value="lista"))
    return buttons


def menu_reply(text: str, **kwargs) -> ChatReply:
    kwargs.setdefault("nextField", "choice")
    kwargs.setdefault("buttons", main_action_buttons())
    return ChatReply(reply=text, **kwargs)


def format_when(value: Optional[datetime]) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime("%d.%m %H:%M")


def orders_for_session(session_id: str) -> List[Order]:
    conv = get_conversation(session_id)
    visitor_id = conv["visitorId"] if conv else session_id
    session_ids = conversation_ids_for_visitor(visitor_id)
    mine = [order for order in orders.values() if order.createdBySession in session_ids]
    mine.sort(key=lambda order: order.createdAt, reverse=True)
    return mine


def order_choice_line(index: int, order: Order) -> str:
    data = order.data or {}
    client = data.get("client_name") or "bez nazwy"
    pickup = data.get("pickup") or "-"
    delivery = data.get("delivery") or "-"
    event = "czeka na wycenę"
    if order.status.lower() == "anulowane":
        event = "anulowane"
    elif order.offer:
        if order.offer.accepted is True:
            event = "oferta zaakceptowana"
        elif order.offer.accepted is False:
            event = "oferta odrzucona"
        else:
            event = f"oferta {order.offer.price} oczekuje"
    return f"{index}. {client} | {pickup} → {delivery} | {order.status} | {event}"


def order_events(order: Order) -> List[str]:
    data = order.data or {}
    client = data.get("client_name") or "Zlecenie"
    created = format_when(order.createdAt)
    if created:
        events = [f"{created} — {client}: zlecenie utworzone"]
    else:
        events = [f"{client}: zlecenie utworzone"]
    if order.offer:
        price = order.offer.price or "bez ceny"
        if order.offer.accepted is True:
            when = format_when(order.offer.acceptedAt)
            prefix = f"{when} — " if when else ""
            events.append(f"{prefix}{client}: oferta {price} zaakceptowana")
        elif order.offer.accepted is False:
            events.append(f"{client}: oferta {price} odrzucona")
        else:
            events.append(f"{client}: oferta {price}, oczekuje na akceptację")
    elif order.status.lower() != "anulowane":
        events.append(f"{client}: czeka na wycenę")
    if order.status.lower() == "anulowane":
        events.append(f"{client}: zlecenie anulowane")
    return events


def order_pick_buttons(listed: List[Order]) -> List[ChatButton]:
    buttons: List[ChatButton] = []
    for index, order in enumerate(listed[:6], start=1):
        client = (order.data or {}).get("client_name") or f"zlecenie {index}"
        buttons.append(ChatButton(label=f"{index}. {client}", value=str(index)))
    buttons.extend(main_action_buttons())
    return buttons


def show_session_orders(session_id: str, intro: str) -> ChatReply:
    state = sessions[session_id]
    listed = orders_for_session(session_id)
    state["mode"] = "select_order"
    state["listed_ids"] = [order.id for order in listed]
    if not listed:
        return ChatReply(
            reply=(
                f"{intro}\n\n"
                "Nie mam jeszcze zleceń w tej rozmowie. Mogę założyć nowe albo przyjąć ID zlecenia, jeśli je masz."
            ),
            nextField="order_id",
            buttons=main_action_buttons(),
        )

    lines = [intro, "", "Twoje zlecenia:"]
    lines.extend(order_choice_line(index, order) for index, order in enumerate(listed, start=1))
    lines.extend(["", "Ostatnie zdarzenia:"])
    events: List[str] = []
    for order in listed:
        events.extend(order_events(order))
    lines.extend(f"• {item}" for item in events[:8])
    lines.extend(["", "Wybierz numer zlecenia albo inną akcję."])
    return ChatReply(
        reply="\n".join(lines),
        nextField="order_id",
        buttons=order_pick_buttons(listed),
    )


def open_order_card(session_id: str, order: Order) -> ChatReply:
    state = sessions[session_id]
    state["edit_order_id"] = order.id
    state["mode"] = "order_card"
    summary = format_summary(order.data)
    events = "\n".join(f"• {item}" for item in order_events(order))
    return ChatReply(
        reply=(
            f"Zlecenie {order.id} ({order.status}):\n{summary}\n\n"
            f"Zdarzenia:\n{events}\n\n"
            "Co chcesz z tym zrobić?"
        ),
        nextField="field",
        collected=order.data,
        orderId=order.id,
        buttons=[
            ChatButton(label="Zmień dane", value="edytuj"),
            ChatButton(label="Anuluj zlecenie", value="usuń"),
            ChatButton(label="Powrót do listy", value="lista"),
            ChatButton(label="Nowe zlecenie", value="nowe"),
        ],
    )


def resolve_order_pick(message: str, listed_ids: List[str]) -> Optional[Order]:
    text = message.strip()
    if text in orders:
        return orders[text]
    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(listed_ids):
            return orders.get(listed_ids[index])
    return None


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
    return run_chat(payload, request)


class ConversationCreate(BaseModel):
    visitorId: str


def resolve_chat_identity(payload: ChatRequest) -> tuple[str, str]:
    visitor_id = (payload.visitorId or payload.sessionId or "").strip()
    conversation_id = (payload.conversationId or payload.sessionId or "").strip()
    if not visitor_id:
        visitor_id = str(uuid.uuid4())
    if conversation_id:
        existing = get_conversation(conversation_id)
        if existing and existing.get("visitorId") == visitor_id:
            if existing.get("agentState") and existing["id"] not in sessions:
                sessions[existing["id"]] = existing["agentState"]
            return visitor_id, existing["id"]
    conv = ensure_conversation(visitor_id, conversation_id or None)
    if conv.get("agentState") and conv["id"] not in sessions:
        sessions[conv["id"]] = conv["agentState"]
    return visitor_id, conv["id"]


def run_chat(payload: ChatRequest, request: Optional[Request] = None) -> ChatReply:
    visitor_id, conv_id = resolve_chat_identity(payload)
    message = payload.message.strip()
    user_display = (payload.userDisplay or message).strip()
    skip_user = message.lower() in {"start", "hej", "cześć", "czesc", "menu", ""}
    if message and not skip_user:
        append_message(conv_id, "user", user_display)
        maybe_update_title(conv_id, message)
    routed = ChatRequest(
        sessionId=conv_id,
        visitorId=visitor_id,
        conversationId=conv_id,
        message=payload.message,
    )
    reply = handle_chat_message(routed, request)
    buttons = [button.model_dump() for button in (reply.buttons or [])]
    append_message(conv_id, "bot", reply.reply, buttons)
    save_agent_state(conv_id, sessions.get(conv_id))
    reply.conversationId = conv_id
    return reply


def handle_chat_message(payload: ChatRequest, request: Optional[Request] = None) -> ChatReply:
    session_id = payload.sessionId.strip()
    message = payload.message.strip()

    state = sessions.get(session_id) or reset_session(session_id)
    message_lower = message.lower()

    def start_new_order() -> ChatReply:
        state["mode"] = "new"
        state["step"] = 0
        state["fields"] = {}
        key, prompt = FIELDS[0]
        return ChatReply(
            reply=f"Krok 1/{len(FIELDS)}. {prompt}",
            nextField=key,
            buttons=[ChatButton(label="Anuluj", value="restart")],
        )

    def begin_field_edit() -> ChatReply:
        order_id = state.get("edit_order_id")
        order = orders.get(order_id)
        if not order:
            return menu_reply("Nie mam tego zlecenia. Wybierz inną akcję.")
        state["mode"] = "edit_choose_field"
        options = field_options_text()
        return ChatReply(
            reply=(
                f"Które pole chcesz zmienić?\n({options})\n"
                "Możesz też anulować zlecenie."
            ),
            nextField="field",
            collected=order.data,
            orderId=order.id,
            buttons=field_buttons(),
        )

    # If we already finished a flow, start fresh
    if state.get("mode") == "done":
        state = reset_session(session_id)

    # Allow manual reset
    if message_lower in RESET_COMMANDS:
        state = reset_session(session_id)
        return menu_reply(f"Sesja wyzerowana.\n\n{initial_prompt()}")

    # Pending offer acceptance flow override
    pending_order = acceptance_pending.get(session_id) or state.get("pending_accept_order")
    if pending_order:
        bypass_cmds = NEW_COMMANDS | EDIT_COMMANDS | LIST_COMMANDS | BACK_COMMANDS
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
            return menu_reply("Oferta wygasła lub zlecenie nie istnieje.\n\n" + initial_prompt())
        if order.status.lower() == "anulowane":
            acceptance_pending.pop(session_id, None)
            state = reset_session(session_id)
            return menu_reply("Zlecenie jest anulowane.\n\n" + initial_prompt())

        if message_lower in YES_COMMANDS:
            order.offer.accepted = True
            order.offer.acceptedAt = utcnow()
            orders[order.id] = order
            persist_store()
            acceptance_pending.pop(session_id, None)
            state["mode"] = "done"
            summary = format_summary(order.data)
            return menu_reply(
                (
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
            return menu_reply(
                "Oferta odrzucona. Jeśli chcesz nową wycenę, otwórz listę zleceń albo popraw dane.",
                orderId=order.id,
            )

        return ChatReply(
            reply="Czy akceptujesz ofertę?",
            nextField="confirm_offer",
            buttons=yes_no_buttons(),
        )

    if message_lower in LIST_COMMANDS | BACK_COMMANDS and state["mode"] not in {"new", "edit_new_value"}:
        return show_session_orders(session_id, "Oto Twoje zlecenia i zdarzenia z tej rozmowy.")

    if message_lower in NEW_COMMANDS and state["mode"] not in {"new", "offer_confirm"}:
        return start_new_order()

    if message_lower in EDIT_COMMANDS and state["mode"] not in {"new", "edit_new_value", "edit_choose_field", "order_card"}:
        return show_session_orders(session_id, "Które zlecenie chcesz zmienić?")

    # Ask for choice if no mode yet
    if state["mode"] is None:
        if not message or message_lower in START_COMMANDS:
            return menu_reply(initial_prompt())

        if "zmien" in message_lower or "edyt" in message_lower:
            return show_session_orders(session_id, "Które zlecenie chcesz zmienić?")

        return menu_reply(
            "Nie rozpoznałem tej prośby. Mogę założyć zlecenie, pokazać listę i zdarzenia albo poprawić istniejące dane."
        )

    if state["mode"] == "select_order":
        picked = resolve_order_pick(message, state.get("listed_ids") or [])
        if picked:
            return open_order_card(session_id, picked)
        query = message_lower
        filtered = [
            order
            for order in orders_for_session(session_id)
            if query in (order.data.get("client_name") or "").lower()
            or query in order.id.lower()
        ]
        if filtered:
            state["listed_ids"] = [order.id for order in filtered]
            lines = ["Znalazłem takie zlecenia:", ""]
            lines.extend(order_choice_line(index, order) for index, order in enumerate(filtered, start=1))
            lines.append("\nWybierz numer.")
            return ChatReply(
                reply="\n".join(lines),
                nextField="order_id",
                buttons=order_pick_buttons(filtered),
            )
        return ChatReply(
            reply="Nie znalazłem takiego zlecenia. Wybierz numer z listy, podaj ID albo załóż nowe.",
            nextField="order_id",
            buttons=order_pick_buttons(orders_for_session(session_id)),
        )

    if state["mode"] == "order_card":
        if message_lower in EDIT_COMMANDS or message_lower in {"zmień dane", "zmien dane"}:
            return begin_field_edit()
        if message_lower in DELETE_COMMANDS:
            state["mode"] = "delete_confirm"
            return ChatReply(
                reply=f"Czy na pewno anulować zlecenie {state['edit_order_id']}?",
                nextField="confirm_delete",
                orderId=state["edit_order_id"],
                buttons=yes_no_buttons(),
            )
        order = orders.get(state.get("edit_order_id"))
        if order:
            return open_order_card(session_id, order)
        return show_session_orders(session_id, "To zlecenie jest niedostępne. Oto lista z tej rozmowy.")

    # EDIT FLOW: choose field
    if state["mode"] == "edit_choose_field":
        field_key = message_lower.strip()
        if field_key in DELETE_COMMANDS:
            state["mode"] = "delete_confirm"
            return ChatReply(
                reply=f"Czy na pewno usunąć zlecenie {state['edit_order_id']}?",
                nextField="confirm_delete",
                orderId=state["edit_order_id"],
                buttons=yes_no_buttons(),
            )
        resolved = resolve_field_key(field_key)
        if not resolved:
            options = field_options_text()
            return ChatReply(
                reply=f"Nie znam takiego pola. Wybierz jedno z: {options}",
                nextField="field",
                buttons=field_buttons(),
            )
        state["edit_field"] = resolved
        state["mode"] = "edit_new_value"
        return ChatReply(
            reply=f"Podaj nową wartość dla „{field_label(resolved)}”:",
            nextField=resolved,
            buttons=[ChatButton(label="Powrót", value="lista")],
        )

    if state["mode"] == "delete_confirm":
        order_id = state.get("edit_order_id")
        order = orders.get(order_id)
        if not order_id or not order:
            state = reset_session(session_id)
            return menu_reply("Zlecenie nie istnieje. Zacznij od nowa.")
        if message_lower in YES_COMMANDS:
            order.status = "Anulowane"
            for sid, oid in list(acceptance_pending.items()):
                if oid == order_id:
                    acceptance_pending.pop(sid, None)
            orders[order_id] = order
            persist_store()
            state["mode"] = "done"
            return menu_reply(
                f"Zlecenie {order_id} oznaczone jako „Anulowane”. Co dalej?",
                orderId=order_id,
                collected=order.data,
                done=True,
            )
        if message_lower in NO_COMMANDS:
            return open_order_card(session_id, order)
        return ChatReply(
            reply="Potwierdź usunięcie: tak albo nie.",
            nextField="confirm_delete",
            orderId=order_id,
            buttons=yes_no_buttons(),
        )

    # EDIT FLOW: set new value
    if state["mode"] == "edit_new_value":
        order_id = state["edit_order_id"]
        field_key = state["edit_field"]
        order = orders.get(order_id)
        if not order:
            state = reset_session(session_id)
            return menu_reply("Sesja wygasła, zacznij od nowa.")
        order.data[field_key] = message
        orders[order_id] = order
        persist_store()
        state["mode"] = "done"
        summary = format_summary(order.data)
        return menu_reply(
            f"Zaktualizowano zlecenie {order_id}.\nNowe dane:\n{summary}\nCo dalej?",
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
                f"Zlecenie zapisane i czeka na wycenę.\n"
                f"ID: {order_id}\n"
                f"Link do podglądu: {view_url}\n\n"
                "Możesz teraz otworzyć listę zleceń albo dodać kolejne."
            )
            return menu_reply(reply_text, done=True, orderId=order_id, collected=state["fields"])
        if message_lower in NO_COMMANDS:
            state = reset_session(session_id)
            return menu_reply(f"Odrzucono.\n\n{initial_prompt()}")

        summary = format_summary(state["fields"])
        return ChatReply(
            reply=f"Potwierdź tak albo nie.\n{summary}",
            nextField="confirm",
            collected=state["fields"],
            buttons=yes_no_buttons(),
        )

    # NEW FLOW: Regular field collection
    if state["mode"] == "new":
        current_key, current_prompt = FIELDS[state["step"]]
        if message:
            state["fields"][current_key] = message
            state["step"] += 1

        cancel_btn = [ChatButton(label="Anuluj", value="restart")]
        if state["step"] < len(FIELDS):
            next_key, next_prompt = FIELDS[state["step"]]
            step_no = state["step"] + 1
            extra_buttons = list(cancel_btn)
            if next_key == "requirements":
                extra_buttons.insert(0, ChatButton(label="Brak wymagań", value="brak"))
            return ChatReply(
                reply=f"Krok {step_no}/{len(FIELDS)}. {next_prompt}",
                nextField=next_key,
                collected=state["fields"],
                buttons=extra_buttons,
            )

        summary = format_summary(state["fields"])
        return ChatReply(
            reply=f"Podsumowanie:\n{summary}\nPotwierdzasz?",
            nextField="confirm",
            collected=state["fields"],
            buttons=yes_no_buttons(),
        )

    # Fallback: start over choice
    state = reset_session(session_id)
    return menu_reply(initial_prompt())


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
def get_notifications(sessionId: str = "", conversationId: str = "") -> Dict[str, List[str]]:
    key = (conversationId or sessionId).strip()
    msgs = session_notifications.pop(key, [])
    return {"messages": msgs}


@app.get("/chat/conversations")
def api_list_conversations(visitorId: str) -> Dict[str, List[Dict]]:
    items = [serialize_conversation(conv) for conv in list_for_visitor(visitorId.strip())]
    return {"conversations": items}


@app.post("/chat/conversations")
def api_create_conversation(payload: ConversationCreate) -> Dict:
    visitor_id = payload.visitorId.strip()
    if not visitor_id:
        raise HTTPException(status_code=400, detail="visitorId is required")
    conv = create_conversation(visitor_id)
    return serialize_conversation(conv, include_messages=True)


@app.get("/chat/conversations/{conversation_id}")
def api_get_conversation(conversation_id: str, visitorId: str) -> Dict:
    conv = get_conversation(conversation_id)
    if not conv or conv.get("visitorId") != visitorId.strip():
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.get("agentState"):
        sessions[conv["id"]] = conv["agentState"]
    return serialize_conversation(conv, include_messages=True)


@app.post("/chat/conversations/{conversation_id}/reopen")
def api_reopen_conversation(conversation_id: str, payload: ConversationCreate) -> Dict:
    conv = reopen_conversation(payload.visitorId.strip(), conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.get("agentState"):
        sessions[conv["id"]] = conv["agentState"]
    return serialize_conversation(conv, include_messages=True)


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
                reply = run_chat(ChatRequest(sessionId=from_id, visitorId=from_id, message=text))
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
    reply = run_chat(ChatRequest(sessionId=session_id, visitorId=session_id, message=Body))
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
