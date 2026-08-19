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


class YardRequest(BaseModel):
    id: str
    createdAt: datetime
    createdBySession: Optional[str] = None
    kind: str
    kindLabel: str
    status: str = "Oczekuje"
    data: Dict[str, str]


class YardStatusUpdate(BaseModel):
    status: str


class Visit(BaseModel):
    id: str
    driver_name: str
    plates: str
    stage: str
    updatedAt: datetime


class VisitStageUpdate(BaseModel):
    stage: Optional[str] = None
    advance: bool = False


# In-memory stores (swap to Redis/DB later)
orders: Dict[str, Order] = {}
yard_requests: Dict[str, YardRequest] = {}
visits: Dict[str, Visit] = {}
sessions: Dict[str, Dict] = {}
session_notifications: Dict[str, List[str]] = {}
acceptance_pending: Dict[str, str] = {}
admin_sessions: set[str] = set()

data_path = Path(__file__).parent / "data"
data_path.mkdir(exist_ok=True)
ORDERS_FILE = data_path / "orders.json"
YARD_FILE = data_path / "yard_requests.json"
VISITS_FILE = data_path / "visits.json"
static_path = Path(__file__).parent / "static"
static_path.mkdir(parents=True, exist_ok=True)

WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "qqq")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}

NEW_COMMANDS = {"n", "nowe", "nowa", "nowy", "nowe zlecenie"}
YARD_COMMANDS = {
    "park",
    "teren parku",
    "jestem na terenie parku",
    "na terenie parku",
    "dyspozytor",
    "kierowca",
    "awizacja",
}
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
YARD_BUSY_MODES = {
    "location_check",
    "dispatcher",
    "visit_confirm",
    "yard_onsite",
    "yard_driver",
    "yard_plates",
    "yard_kind",
    "yard_detail",
    "yard_confirm",
}
YARD_KINDS = {
    "wczesniejszy_zaladunek": (
        "Wcześniejszy załadunek",
        "Podaj nowy, wcześniejszy termin załadunku (data i godzina).",
    ),
    "pozniejszy_zaladunek": (
        "Późniejszy załadunek",
        "Podaj nowy, późniejszy termin załadunku (data i godzina).",
    ),
    "wczesniejszy_rozladunek": (
        "Wcześniejszy rozładunek",
        "Podaj nowy, wcześniejszy termin rozładunku (data i godzina).",
    ),
    "pozniejszy_rozladunek": (
        "Późniejszy rozładunek",
        "Podaj nowy, późniejszy termin rozładunku (data i godzina).",
    ),
    "pauza": (
        "Pauza / dodatkowy postój",
        "Jak długo ma trwać dodatkowy postój na terenie parku? (np. 45 min albo do 14:30)",
    ),
    "naczepa": (
        "Pozostawienie naczepy",
        "Podaj datę i godzinę odbioru naczepy.",
    ),
}
YARD_KIND_ALIASES = {
    "wcześniejszy załadunek": "wczesniejszy_zaladunek",
    "wczesniejszy zaladunek": "wczesniejszy_zaladunek",
    "późniejszy załadunek": "pozniejszy_zaladunek",
    "pozniejszy zaladunek": "pozniejszy_zaladunek",
    "wcześniejszy rozładunek": "wczesniejszy_rozladunek",
    "wczesniejszy rozladunek": "wczesniejszy_rozladunek",
    "późniejszy rozładunek": "pozniejszy_rozladunek",
    "pozniejszy rozladunek": "pozniejszy_rozladunek",
    "pauza": "pauza",
    "postój": "pauza",
    "postoj": "pauza",
    "naczepa": "naczepa",
    "pozostawienie naczepy": "naczepa",
    "zostawiam naczepę": "naczepa",
    "zostawiam naczepę": "naczepa",
}
POSTEP_COMMANDS = {
    "postep",
    "postęp",
    "sprawdzenie",
    "sprawdzenie postępów wizyty",
    "sprawdzenie postepow wizyty",
    "status wizyty",
    "postępy",
    "postepy",
    "postępy wizyty",
    "postepy wizyty",
}
LEAVE_SITE_COMMANDS = {
    "leave_site",
    "nie jestem już na placu",
    "nie jestem juz na placu",
    "nie jestem na placu",
    "wyjazd",
}
VISIT_STAGES = [
    ("rozpoczeta", "wizyta rozpoczęta"),
    ("dokumenty", "potwierdzone dokumenty"),
    ("dok", "przypisany dok i przekazane do realizacji"),
    ("zaladunek", "zakończony załadunek/rozładunek"),
    ("dokumenty_wyjazd", "przygotowane dokumenty"),
]
VISIT_STAGE_LABELS = {key: label for key, label in VISIT_STAGES}
VISIT_STAGE_KEYS = [key for key, _ in VISIT_STAGES]


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


def yard_to_dict(item: YardRequest) -> Dict:
    return {
        "id": item.id,
        "createdAt": item.createdAt.isoformat(),
        "createdBySession": item.createdBySession,
        "kind": item.kind,
        "kindLabel": item.kindLabel,
        "status": item.status,
        "data": item.data,
    }


def dict_to_yard(data: Dict) -> YardRequest:
    return YardRequest(
        id=data["id"],
        createdAt=parse_datetime(data.get("createdAt")) or utcnow(),
        createdBySession=data.get("createdBySession"),
        kind=data.get("kind", ""),
        kindLabel=data.get("kindLabel", ""),
        status=data.get("status", "Oczekuje"),
        data=data.get("data", {}),
    )


def persist_yard() -> None:
    payload = [yard_to_dict(item) for item in yard_requests.values()]
    tmp = YARD_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp.replace(YARD_FILE)


def visit_to_dict(item: Visit) -> Dict:
    return {
        "id": item.id,
        "driver_name": item.driver_name,
        "plates": item.plates,
        "stage": item.stage,
        "updatedAt": item.updatedAt.isoformat(),
    }


def dict_to_visit(data: Dict) -> Visit:
    return Visit(
        id=data["id"],
        driver_name=data.get("driver_name", ""),
        plates=data.get("plates", ""),
        stage=data.get("stage", VISIT_STAGE_KEYS[0]),
        updatedAt=parse_datetime(data.get("updatedAt")) or utcnow(),
    )


def persist_visits() -> None:
    payload = [visit_to_dict(item) for item in visits.values()]
    tmp = VISITS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp.replace(VISITS_FILE)


def seed_demo_visits() -> None:
    visits.clear()
    now = utcnow()
    demo = [
        Visit(
            id="vis-1",
            driver_name="Jan Kowalski",
            plates="WZ 1234A / WZ 5678B",
            stage="dok",
            updatedAt=now,
        ),
        Visit(
            id="vis-2",
            driver_name="Anna Nowak",
            plates="KR 9A111",
            stage="dokumenty",
            updatedAt=now,
        ),
        Visit(
            id="vis-3",
            driver_name="Piotr Zieliński",
            plates="PO 2222T",
            stage="zaladunek",
            updatedAt=now,
        ),
    ]
    for item in demo:
        visits[item.id] = item


def load_store() -> None:
    if ORDERS_FILE.exists():
        try:
            loaded = json.loads(ORDERS_FILE.read_text())
            for item in loaded:
                o = dict_to_order(item)
                orders[o.id] = o
        except Exception:
            pass
    if YARD_FILE.exists():
        try:
            loaded = json.loads(YARD_FILE.read_text())
            for item in loaded:
                req = dict_to_yard(item)
                yard_requests[req.id] = req
        except Exception:
            pass
    visits.clear()
    if VISITS_FILE.exists():
        try:
            loaded = json.loads(VISITS_FILE.read_text())
            for item in loaded:
                visit = dict_to_visit(item)
                visits[visit.id] = visit
        except Exception:
            pass
    if not visits:
        seed_demo_visits()
        try:
            persist_visits()
        except Exception:
            pass


def reset_runtime_state() -> None:
    """Used by tests to isolate cases."""
    orders.clear()
    yard_requests.clear()
    visits.clear()
    sessions.clear()
    session_notifications.clear()
    acceptance_pending.clear()
    admin_sessions.clear()
    reset_conversations()
    seed_demo_visits()


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
        "yard_kind": None,
        "yard_fields": {},
        "on_site": None,
        "pending_visit_check": False,
    }
    return sessions[session_id]


def format_summary(fields: Dict[str, str]) -> str:
    lines = [f"- {field_label(key)}: {value}" for key, value in fields.items()]
    return "\n".join(lines)


def location_prompt() -> str:
    return (
        "Cześć. Jestem automatycznym agentem — mogę pełnić rolę spedytora "
        "albo dyspozytora parku.\n\n"
        "Na początek: czy jesteś teraz na placu (terenie parku logistycznego)?"
    )


def spedytor_prompt() -> str:
    return (
        "Pomogę Ci jako spedytor. Umiem:\n"
        "- przyjąć nowe zlecenie transportowe,\n"
        "- pokazać Twoje zlecenia i zdarzenia (wycena, oferta, akceptacja, anulowanie),\n"
        "- poprawić dane albo anulować zlecenie,\n"
        "- przyjąć decyzję o ofercie, gdy przyjdzie wycena.\n\n"
        "Wybierz akcję albo napisz, czego potrzebujesz."
    )


def initial_prompt() -> str:
    return location_prompt()


def spedytor_action_buttons() -> List[ChatButton]:
    return [
        ChatButton(label="Nowe zlecenie", value="nowe"),
        ChatButton(label="Moje zlecenia", value="lista"),
        ChatButton(label="Zmień zlecenie", value="edytuj"),
        ChatButton(label="Restart", value="restart"),
    ]


def dispatcher_action_buttons() -> List[ChatButton]:
    buttons = [ChatButton(label="Sprawdzenie postępów wizyty", value="postep")]
    buttons.extend(ChatButton(label=label, value=key) for key, (label, _) in YARD_KINDS.items())
    buttons.append(ChatButton(label="Nie jestem już na placu", value="leave_site"))
    buttons.append(ChatButton(label="Restart", value="restart"))
    return buttons


def main_action_buttons(state: Optional[Dict] = None) -> List[ChatButton]:
    if state and state.get("on_site"):
        return dispatcher_action_buttons()
    return spedytor_action_buttons()


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


def yard_kind_buttons() -> List[ChatButton]:
    return dispatcher_action_buttons()


def resolve_yard_kind(message: str) -> Optional[str]:
    msg = message.lower().strip()
    if msg in YARD_KINDS:
        return msg
    return YARD_KIND_ALIASES.get(msg)


def normalize_plates(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def normalize_person_name(value: str) -> str:
    return " ".join((value or "").lower().split())


def find_visit(name: str, plates: str) -> Optional[Visit]:
    plate_key = normalize_plates(plates)
    if plate_key:
        for item in visits.values():
            visit_plates = normalize_plates(item.plates)
            if visit_plates and (plate_key == visit_plates or plate_key in visit_plates or visit_plates in plate_key):
                return item
    name_key = normalize_person_name(name)
    if name_key:
        matches = [
            item for item in visits.values() if normalize_person_name(item.driver_name) == name_key
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def format_visit_progress(visit: Visit) -> str:
    current = visit.stage if visit.stage in VISIT_STAGE_KEYS else VISIT_STAGE_KEYS[0]
    current_index = VISIT_STAGE_KEYS.index(current)
    lines = ["Postęp wizyty:"]
    for index, (_key, label) in enumerate(VISIT_STAGES):
        if index < current_index:
            mark = "✓"
        elif index == current_index:
            mark = "→"
        else:
            mark = "○"
        suffix = "  (aktualny etap)" if index == current_index else ""
        lines.append(f"{mark} {label}{suffix}")
    return "\n".join(lines)


def next_visit_stage(stage: str) -> str:
    if stage not in VISIT_STAGE_KEYS:
        return VISIT_STAGE_KEYS[0]
    index = VISIT_STAGE_KEYS.index(stage)
    return VISIT_STAGE_KEYS[min(index + 1, len(VISIT_STAGE_KEYS) - 1)]


def visit_identity_summary(fields: Dict[str, str]) -> str:
    return (
        f"- Kierowca: {fields.get('driver_name', '-')}\n"
        f"- Pojazd / naczepa: {fields.get('plates', '-')}"
    )


def yard_detail_key(kind: str) -> str:
    if kind == "pauza":
        return "pause_until"
    if kind == "naczepa":
        return "trailer_pickup_at"
    return "requested_time"


def format_yard_summary(fields: Dict[str, str], kind_label: str) -> str:
    lines = [
        f"- Typ zgłoszenia: {kind_label}",
        f"- Kierowca: {fields.get('driver_name', '-')}",
        f"- Pojazd / naczepa: {fields.get('plates', '-')}",
    ]
    if fields.get("requested_time"):
        lines.append(f"- Nowy termin: {fields['requested_time']}")
    if fields.get("pause_until"):
        lines.append(f"- Czas postoju: {fields['pause_until']}")
    if fields.get("trailer_pickup_at"):
        lines.append(f"- Odbiór naczepy: {fields['trailer_pickup_at']}")
    return "\n".join(lines)


def yard_requests_for_session(session_id: str) -> List[YardRequest]:
    conv = get_conversation(session_id)
    visitor_id = conv["visitorId"] if conv else session_id
    session_ids = conversation_ids_for_visitor(visitor_id)
    mine = [item for item in yard_requests.values() if item.createdBySession in session_ids]
    mine.sort(key=lambda item: item.createdAt, reverse=True)
    return mine


def yard_event_line(item: YardRequest) -> str:
    when = format_when(item.createdAt)
    driver = item.data.get("driver_name") or "kierowca"
    return f"{when} — {driver}: {item.kindLabel} ({item.status})"


def menu_reply(text: str, **kwargs) -> ChatReply:
    state = kwargs.pop("state", None)
    kwargs.setdefault("nextField", "choice")
    kwargs.setdefault("buttons", main_action_buttons(state))
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


def order_pick_buttons(listed: List[Order], state: Optional[Dict] = None) -> List[ChatButton]:
    buttons: List[ChatButton] = []
    for index, order in enumerate(listed[:6], start=1):
        client = (order.data or {}).get("client_name") or f"zlecenie {index}"
        buttons.append(ChatButton(label=f"{index}. {client}", value=str(index)))
    buttons.extend(main_action_buttons(state))
    return buttons


def show_session_orders(session_id: str, intro: str) -> ChatReply:
    state = sessions[session_id]
    listed = orders_for_session(session_id)
    state["mode"] = "select_order"
    state["listed_ids"] = [order.id for order in listed]
    if not listed and not yard_requests_for_session(session_id):
        return ChatReply(
            reply=(
                f"{intro}\n\n"
                "Nie mam jeszcze zleceń w tej rozmowie. Mogę założyć nowe, przyjąć zgłoszenie z terenu parku "
                "albo ID zlecenia, jeśli je masz."
            ),
            nextField="order_id",
            buttons=main_action_buttons(state),
        )

    lines = [intro]
    if listed:
        lines.extend(["", "Twoje zlecenia:"])
        lines.extend(order_choice_line(index, order) for index, order in enumerate(listed, start=1))
    lines.extend(["", "Ostatnie zdarzenia:"])
    events: List[str] = []
    for item in yard_requests_for_session(session_id)[:6]:
        events.append(yard_event_line(item))
    for order in listed:
        events.extend(order_events(order))
    if not events:
        events.append("Brak zdarzeń.")
    lines.extend(f"• {item}" for item in events[:10])
    lines.extend(["", "Wybierz numer zlecenia albo inną akcję."])
    return ChatReply(
        reply="\n".join(lines),
        nextField="order_id",
        buttons=order_pick_buttons(listed, state),
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

    def reply_menu(text: str, **kwargs) -> ChatReply:
        return menu_reply(text, state=state, **kwargs)

    def ask_location(prefix: str = "") -> ChatReply:
        state["mode"] = "location_check"
        state["on_site"] = None
        text = location_prompt()
        if prefix:
            text = f"{prefix}\n\n{text}"
        return ChatReply(
            reply=text,
            nextField="location_check",
            buttons=yes_no_buttons(),
        )

    def enter_off_site(intro: Optional[str] = None) -> ChatReply:
        identity = {
            "driver_name": (state.get("yard_fields") or {}).get("driver_name", ""),
            "plates": (state.get("yard_fields") or {}).get("plates", ""),
        }
        state.clear()
        state.update(
            {
                "mode": None,
                "step": 0,
                "fields": {},
                "edit_order_id": None,
                "edit_field": None,
                "pending_accept_order": None,
                "whatsapp": None,
                "listed_ids": [],
                "yard_kind": None,
                "yard_fields": identity,
                "on_site": False,
                "pending_visit_check": False,
            }
        )
        sessions[session_id] = state
        return reply_menu(intro or spedytor_prompt())

    def dispatcher_menu(intro: Optional[str] = None, **kwargs) -> ChatReply:
        state["on_site"] = True
        state["mode"] = "dispatcher"
        state["yard_kind"] = None
        state["pending_visit_check"] = False
        text = intro or (
            "Jesteś na placu, więc obsługuję Cię jako dyspozytor parku.\n"
            "Co chcesz zrobić?\n"
            "- sprawdzenie postępów wizyty\n"
            "- wcześniejszy lub późniejszy załadunek / rozładunek\n"
            "- pauzę (dodatkowy postój)\n"
            "- pozostawienie naczepy"
        )
        kwargs.setdefault("nextField", "dispatcher")
        kwargs.setdefault("buttons", dispatcher_action_buttons())
        return ChatReply(reply=text, **kwargs)

    def enter_on_site() -> ChatReply:
        state["on_site"] = True
        fields = state.setdefault("yard_fields", {})
        if not (fields.get("driver_name") or "").strip():
            state["mode"] = "yard_driver"
            return ChatReply(
                reply=(
                    "Jesteś na placu, więc obsługuję Cię jako dyspozytor parku.\n\n"
                    "Podaj imię i nazwisko kierowcy."
                ),
                nextField="driver_name",
                buttons=[ChatButton(label="Anuluj", value="restart")],
            )
        if not (fields.get("plates") or "").strip():
            state["mode"] = "yard_plates"
            return ChatReply(
                reply="Podaj numer rejestracyjny ciągnika i naczepy.",
                nextField="plates",
                buttons=[ChatButton(label="Anuluj", value="restart")],
            )
        return dispatcher_menu()

    def visit_progress_reply() -> ChatReply:
        fields = state.get("yard_fields") or {}
        visit = find_visit(fields.get("driver_name", ""), fields.get("plates", ""))
        state["mode"] = "dispatcher"
        state["pending_visit_check"] = False
        identity = visit_identity_summary(fields)
        if not visit:
            return ChatReply(
                reply=(
                    "Sprawdziłem podane dane:\n"
                    f"{identity}\n\n"
                    "Nie znalazłem wizyty dla tego kierowcy i pojazdu. "
                    "Nie zakładam nowej wizyty — popraw dane albo zgłoś się do biura bramy."
                ),
                nextField="dispatcher",
                buttons=dispatcher_action_buttons(),
            )
        return ChatReply(
            reply=(
                "Dane kierowcy i pojazdu potwierdzone:\n"
                f"{identity}\n\n"
                f"{format_visit_progress(visit)}"
            ),
            nextField="dispatcher",
            buttons=dispatcher_action_buttons(),
        )

    def start_visit_check() -> ChatReply:
        fields = state.setdefault("yard_fields", {})
        if not fields.get("driver_name") or not fields.get("plates"):
            state["pending_visit_check"] = True
            return enter_on_site()
        state["mode"] = "visit_confirm"
        state["pending_visit_check"] = True
        return ChatReply(
            reply=(
                "Sprawdzenie postępów wizyty. Potwierdź dane kierowcy i pojazdu:\n"
                f"{visit_identity_summary(fields)}\n\n"
                "Czy to poprawne?"
            ),
            nextField="visit_confirm",
            buttons=yes_no_buttons() + [ChatButton(label="Popraw dane", value="nie")],
        )

    def handle_dispatcher_choice() -> Optional[ChatReply]:
        if message_lower in LEAVE_SITE_COMMANDS:
            return enter_off_site(
                "OK. Nie jesteś już na placu, więc pomogę Ci jako spedytor.\n\n" + spedytor_prompt()
            )
        if message_lower in POSTEP_COMMANDS:
            return start_visit_check()
        kind = resolve_yard_kind(message)
        if kind:
            label, prompt = YARD_KINDS[kind]
            state["yard_kind"] = kind
            state["yard_fields"]["kind"] = kind
            state["yard_fields"]["kind_label"] = label
            state["mode"] = "yard_detail"
            return ChatReply(
                reply=prompt,
                nextField=yard_detail_key(kind),
                buttons=[ChatButton(label="Anuluj", value="restart")],
            )
        if message_lower in NEW_COMMANDS | EDIT_COMMANDS | LIST_COMMANDS | BACK_COMMANDS:
            return dispatcher_menu(
                "Na placu obsługuję Cię jako dyspozytor. "
                "Żeby złożyć lub zmienić zlecenie spedycyjne, wybierz „Nie jestem już na placu”."
            )
        return None

    def after_identity_collected() -> ChatReply:
        if state.get("pending_visit_check"):
            return visit_progress_reply()
        return dispatcher_menu()

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
            return reply_menu("Nie mam tego zlecenia. Wybierz inną akcję.")
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

    # If we already finished a flow, keep location context
    if state.get("mode") == "done":
        on_site = state.get("on_site")
        yard_fields = dict(state.get("yard_fields") or {})
        state = reset_session(session_id)
        state["on_site"] = on_site
        if on_site:
            state["yard_fields"] = {
                "driver_name": yard_fields.get("driver_name", ""),
                "plates": yard_fields.get("plates", ""),
            }
            state["mode"] = "dispatcher"

    # Allow manual reset
    if message_lower in RESET_COMMANDS:
        state = reset_session(session_id)
        return ask_location("Sesja wyzerowana.")

    # Pending offer acceptance flow override
    pending_order = acceptance_pending.get(session_id) or state.get("pending_accept_order")
    if pending_order:
        bypass_cmds = NEW_COMMANDS | EDIT_COMMANDS | LIST_COMMANDS | BACK_COMMANDS | YARD_COMMANDS
        if message_lower in bypass_cmds:
            acceptance_pending.pop(session_id, None)
            on_site = state.get("on_site")
            state = reset_session(session_id)
            state["on_site"] = on_site
        else:
            state["mode"] = "offer_confirm"
            state["pending_accept_order"] = pending_order

    if state["mode"] == "offer_confirm":
        order = orders.get(state["pending_accept_order"])
        if not order or not order.offer:
            acceptance_pending.pop(session_id, None)
            on_site = state.get("on_site")
            state = reset_session(session_id)
            state["on_site"] = on_site
            return reply_menu("Oferta wygasła lub zlecenie nie istnieje.\n\n" + spedytor_prompt())
        if order.status.lower() == "anulowane":
            acceptance_pending.pop(session_id, None)
            on_site = state.get("on_site")
            state = reset_session(session_id)
            state["on_site"] = on_site
            return reply_menu("Zlecenie jest anulowane.\n\n" + spedytor_prompt())

        if message_lower in YES_COMMANDS:
            order.offer.accepted = True
            order.offer.acceptedAt = utcnow()
            orders[order.id] = order
            persist_store()
            acceptance_pending.pop(session_id, None)
            state["mode"] = "done"
            summary = format_summary(order.data)
            return reply_menu(
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
            return reply_menu(
                "Oferta odrzucona. Jeśli chcesz nową wycenę, otwórz listę zleceń albo popraw dane.",
                orderId=order.id,
            )

        return ChatReply(
            reply="Czy akceptujesz ofertę?",
            nextField="confirm_offer",
            buttons=yes_no_buttons(),
        )

    on_site_answers = YES_COMMANDS | YARD_COMMANDS | {"plac", "na placu", "jestem na placu"}
    if state.get("on_site") is None or state["mode"] == "location_check":
        if message_lower in on_site_answers:
            return enter_on_site()
        if message_lower in NO_COMMANDS:
            return enter_off_site()
        if not message or message_lower in START_COMMANDS or state["mode"] in {None, "location_check"}:
            return ask_location()

    if state.get("on_site") and message_lower in LEAVE_SITE_COMMANDS:
        return enter_off_site(
            "OK. Nie jesteś już na placu, więc pomogę Ci jako spedytor.\n\n" + spedytor_prompt()
        )

    if message_lower in LIST_COMMANDS | BACK_COMMANDS and state["mode"] not in ({"new", "edit_new_value"} | YARD_BUSY_MODES):
        if state.get("on_site"):
            return dispatcher_menu(
                "Na placu obsługuję Cię jako dyspozytor. "
                "Żeby zobaczyć zlecenia spedycyjne, wybierz „Nie jestem już na placu”."
            )
        return show_session_orders(session_id, "Oto Twoje zlecenia i zdarzenia z tej rozmowy.")

    if message_lower in NEW_COMMANDS and state["mode"] not in ({"new", "offer_confirm"} | YARD_BUSY_MODES):
        if state.get("on_site"):
            return dispatcher_menu(
                "Na placu obsługuję Cię jako dyspozytor. "
                "Żeby złożyć zlecenie spedycyjne, wybierz „Nie jestem już na placu”."
            )
        return start_new_order()

    if message_lower in YARD_COMMANDS and state["mode"] not in ({"new", "offer_confirm"} | YARD_BUSY_MODES):
        if state.get("on_site"):
            return dispatcher_menu()
        return reply_menu(
            "Te zgłoszenia przyjmuję tylko od kierowców już na terenie parku. "
            "Jeśli właśnie wjechałeś na plac, wybierz Restart i potwierdź obecność."
        )

    if message_lower in EDIT_COMMANDS and state["mode"] not in ({"new", "edit_new_value", "edit_choose_field", "order_card"} | YARD_BUSY_MODES):
        if state.get("on_site"):
            return dispatcher_menu(
                "Na placu obsługuję Cię jako dyspozytor. "
                "Żeby zmienić zlecenie spedycyjne, wybierz „Nie jestem już na placu”."
            )
        return show_session_orders(session_id, "Które zlecenie chcesz zmienić?")

    if state["mode"] == "visit_confirm":
        if message_lower in YES_COMMANDS:
            return visit_progress_reply()
        if message_lower in NO_COMMANDS:
            state["pending_visit_check"] = True
            state["yard_fields"]["driver_name"] = ""
            state["yard_fields"]["plates"] = ""
            state["mode"] = "yard_driver"
            return ChatReply(
                reply="Podaj imię i nazwisko kierowcy.",
                nextField="driver_name",
                buttons=[ChatButton(label="Anuluj", value="restart")],
            )
        return ChatReply(
            reply="Potwierdź dane kierowcy i pojazdu — tak albo nie.",
            nextField="visit_confirm",
            buttons=yes_no_buttons(),
        )

    if state["mode"] in {"dispatcher", "yard_kind"}:
        handled = handle_dispatcher_choice()
        if handled:
            return handled
        return dispatcher_menu("Wybierz jedną z opcji dyspozytora.")

    if state["mode"] == "yard_driver":
        if not message:
            return ChatReply(reply="Podaj imię i nazwisko kierowcy.", nextField="driver_name")
        state.setdefault("yard_fields", {})["driver_name"] = message
        state["mode"] = "yard_plates"
        return ChatReply(
            reply="Podaj numer rejestracyjny ciągnika i naczepy.",
            nextField="plates",
            buttons=[ChatButton(label="Anuluj", value="restart")],
        )

    if state["mode"] == "yard_plates":
        if not message:
            return ChatReply(reply="Podaj numer rejestracyjny ciągnika i naczepy.", nextField="plates")
        state.setdefault("yard_fields", {})["plates"] = message
        return after_identity_collected()

    if state["mode"] == "yard_detail":
        if not message:
            kind = state.get("yard_kind")
            prompt = YARD_KINDS.get(kind, ("", "Podaj szczegóły zgłoszenia."))[1]
            return ChatReply(reply=prompt, nextField="yard_detail")
        kind = state.get("yard_kind") or ""
        state["yard_fields"][yard_detail_key(kind)] = message
        state["mode"] = "yard_confirm"
        summary = format_yard_summary(state["yard_fields"], state["yard_fields"].get("kind_label", ""))
        return ChatReply(
            reply=f"Podsumowanie zgłoszenia:\n{summary}\n\nWysłać to do dyspozytora?",
            nextField="confirm",
            collected=state["yard_fields"],
            buttons=yes_no_buttons(),
        )

    if state["mode"] == "yard_confirm":
        if message_lower in YES_COMMANDS:
            kind = state.get("yard_kind") or ""
            label = state["yard_fields"].get("kind_label") or YARD_KINDS.get(kind, ("Zgłoszenie",))[0]
            request_id = str(uuid.uuid4())[:8]
            payload = {
                "driver_name": state["yard_fields"].get("driver_name", ""),
                "plates": state["yard_fields"].get("plates", ""),
                "kind": kind,
                "kind_label": label,
            }
            for key in ("requested_time", "pause_until", "trailer_pickup_at"):
                if state["yard_fields"].get(key):
                    payload[key] = state["yard_fields"][key]
            yard_requests[request_id] = YardRequest(
                id=request_id,
                createdAt=utcnow(),
                createdBySession=session_id,
                kind=kind,
                kindLabel=label,
                status="Oczekuje",
                data=payload,
            )
            persist_yard()
            identity = {
                "driver_name": payload.get("driver_name", ""),
                "plates": payload.get("plates", ""),
            }
            state["on_site"] = True
            state["yard_fields"] = identity
            state["yard_kind"] = None
            summary = format_yard_summary(payload, label)
            return dispatcher_menu(
                f"Zgłoszenie przyjęte. ID: {request_id}\n"
                f"{summary}\n\n"
                "Dyspozytor parku zobaczy je w panelu. Status: oczekuje.\n"
                "Co jeszcze mogę zrobić na placu?",
                collected=payload,
                done=True,
            )
        if message_lower in NO_COMMANDS:
            identity = {
                "driver_name": (state.get("yard_fields") or {}).get("driver_name", ""),
                "plates": (state.get("yard_fields") or {}).get("plates", ""),
            }
            state["yard_fields"] = identity
            return dispatcher_menu("Zgłoszenie anulowane. Co chcesz zrobić na placu?")
        summary = format_yard_summary(state["yard_fields"], state["yard_fields"].get("kind_label", ""))
        return ChatReply(
            reply=f"Potwierdź wysłanie zgłoszenia — tak albo nie.\n{summary}",
            nextField="confirm",
            buttons=yes_no_buttons(),
        )

    # Ask for choice if no mode yet
    if state["mode"] is None:
        if state.get("on_site"):
            return dispatcher_menu()
        if not message or message_lower in START_COMMANDS:
            return reply_menu(spedytor_prompt())

        if "zmien" in message_lower or "edyt" in message_lower:
            return show_session_orders(session_id, "Które zlecenie chcesz zmienić?")

        return reply_menu(
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
                buttons=order_pick_buttons(filtered, state),
            )
        return ChatReply(
            reply="Nie znalazłem takiego zlecenia. Wybierz numer z listy, podaj ID albo załóż nowe.",
            nextField="order_id",
            buttons=order_pick_buttons(orders_for_session(session_id), state),
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
            return reply_menu("Zlecenie nie istnieje. Zacznij od nowa.")
        if message_lower in YES_COMMANDS:
            order.status = "Anulowane"
            for sid, oid in list(acceptance_pending.items()):
                if oid == order_id:
                    acceptance_pending.pop(sid, None)
            orders[order_id] = order
            persist_store()
            state["mode"] = "done"
            return reply_menu(
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
            return reply_menu("Sesja wygasła, zacznij od nowa.")
        order.data[field_key] = message
        orders[order_id] = order
        persist_store()
        state["mode"] = "done"
        summary = format_summary(order.data)
        return reply_menu(
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
            return reply_menu(reply_text, done=True, orderId=order_id, collected=state["fields"])
        if message_lower in NO_COMMANDS:
            state = reset_session(session_id)
            state["on_site"] = False
            return reply_menu(f"Odrzucono.\n\n{spedytor_prompt()}")

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
    if state.get("on_site"):
        return dispatcher_menu()
    if state.get("on_site") is None:
        return ask_location()
    return reply_menu(spedytor_prompt())


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


@app.get("/yard-requests", response_model=Dict[str, YardRequest])
def list_yard_requests(request: Request) -> Dict[str, YardRequest]:
    require_admin(request)
    return yard_requests


@app.post("/yard-requests/{request_id}/status")
def set_yard_status(request_id: str, payload: YardStatusUpdate, request: Request):
    require_admin(request)
    item = yard_requests.get(request_id)
    if not item:
        raise HTTPException(status_code=404, detail="Yard request not found")
    status = payload.status.strip()
    if status not in {"Oczekuje", "Przyjęte", "Odrzucone"}:
        raise HTTPException(status_code=400, detail="Invalid status")
    item.status = status
    yard_requests[request_id] = item
    persist_yard()
    return {"status": "ok"}


@app.get("/visits", response_model=Dict[str, Visit])
def list_visits(request: Request) -> Dict[str, Visit]:
    require_admin(request)
    return visits


@app.post("/visits/{visit_id}/stage")
def set_visit_stage(visit_id: str, payload: VisitStageUpdate, request: Request):
    require_admin(request)
    item = visits.get(visit_id)
    if not item:
        raise HTTPException(status_code=404, detail="Visit not found")
    if payload.advance:
        item.stage = next_visit_stage(item.stage)
    elif payload.stage:
        if payload.stage not in VISIT_STAGE_KEYS:
            raise HTTPException(status_code=400, detail="Invalid stage")
        item.stage = payload.stage
    else:
        raise HTTPException(status_code=400, detail="Provide stage or advance")
    item.updatedAt = utcnow()
    visits[visit_id] = item
    persist_visits()
    return visit_to_dict(item)


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
