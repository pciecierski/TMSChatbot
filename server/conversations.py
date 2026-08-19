from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import json
import uuid


CONVERSATIONS_FILE = Path(__file__).parent / "data" / "conversations.json"
conversations: Dict[str, Dict] = {}

TITLE_HINTS = {
    "park": "Zgłoszenie z parku",
    "teren parku": "Zgłoszenie z parku",
    "jestem na terenie parku": "Zgłoszenie z parku",
    "lista": "Moje zlecenia",
    "edytuj": "Zmiana zlecenia",
    "restart": "Restart rozmowy",
    "postep": "Sprawdzenie wizyty",
    "postęp": "Sprawdzenie wizyty",
    "sprawdzenie": "Sprawdzenie wizyty",
    "tak": "Na placu",
    "nie": "Poza placem",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def persist_conversations() -> None:
    CONVERSATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = list(conversations.values())
    tmp = CONVERSATIONS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp.replace(CONVERSATIONS_FILE)


def load_conversations() -> None:
    conversations.clear()
    if not CONVERSATIONS_FILE.exists():
        return
    try:
        loaded = json.loads(CONVERSATIONS_FILE.read_text())
        for item in loaded:
            if item.get("id"):
                conversations[item["id"]] = item
    except Exception:
        pass


def reset_conversations() -> None:
    conversations.clear()


def serialize_conversation(conv: Dict, include_messages: bool = False) -> Dict:
    data = {
        "id": conv["id"],
        "visitorId": conv["visitorId"],
        "title": conv.get("title") or "Nowa rozmowa",
        "createdAt": conv["createdAt"],
        "updatedAt": conv["updatedAt"],
        "archived": bool(conv.get("archived")),
        "messageCount": len(conv.get("messages") or []),
        "preview": "",
    }
    messages = conv.get("messages") or []
    if messages:
        last = messages[-1]
        data["preview"] = (last.get("text") or "")[:80]
    if include_messages:
        data["messages"] = messages
    return data


def list_for_visitor(visitor_id: str) -> List[Dict]:
    items = [c for c in conversations.values() if c.get("visitorId") == visitor_id]
    items.sort(key=lambda c: c.get("updatedAt") or "", reverse=True)
    return items


def conversation_ids_for_visitor(visitor_id: str) -> set:
    ids = {c["id"] for c in conversations.values() if c.get("visitorId") == visitor_id}
    ids.add(visitor_id)
    return ids


def get_conversation(conversation_id: str) -> Optional[Dict]:
    return conversations.get(conversation_id)


def archive_other_conversations(visitor_id: str, active_id: str) -> None:
    changed = False
    for conv in conversations.values():
        if conv.get("visitorId") == visitor_id and conv["id"] != active_id and not conv.get("archived"):
            conv["archived"] = True
            changed = True
    if changed:
        persist_conversations()


def create_conversation(visitor_id: str, conversation_id: Optional[str] = None) -> Dict:
    archive_other_conversations(visitor_id, active_id="")
    conv_id = conversation_id or str(uuid.uuid4())
    now = utcnow().isoformat()
    conv = {
        "id": conv_id,
        "visitorId": visitor_id,
        "title": "Nowa rozmowa",
        "createdAt": now,
        "updatedAt": now,
        "archived": False,
        "agentState": None,
        "messages": [],
    }
    conversations[conv_id] = conv
    persist_conversations()
    return conv


def ensure_conversation(visitor_id: str, conversation_id: Optional[str] = None) -> Dict:
    if conversation_id and conversation_id in conversations:
        conv = conversations[conversation_id]
        if conv.get("visitorId") == visitor_id:
            return conv
    active = next((c for c in list_for_visitor(visitor_id) if not c.get("archived")), None)
    if active:
        return active
    return create_conversation(visitor_id, conversation_id)


def reopen_conversation(visitor_id: str, conversation_id: str) -> Optional[Dict]:
    conv = conversations.get(conversation_id)
    if not conv or conv.get("visitorId") != visitor_id:
        return None
    archive_other_conversations(visitor_id, conversation_id)
    conv["archived"] = False
    conv["updatedAt"] = utcnow().isoformat()
    persist_conversations()
    return conv


def append_message(conversation_id: str, role: str, text: str, buttons: Optional[List[Dict]] = None) -> None:
    conv = conversations.get(conversation_id)
    if not conv:
        return
    conv.setdefault("messages", []).append(
        {
            "id": str(uuid.uuid4()),
            "role": role,
            "text": text,
            "buttons": buttons or [],
            "createdAt": utcnow().isoformat(),
        }
    )
    conv["updatedAt"] = utcnow().isoformat()


def maybe_update_title(conversation_id: str, user_text: str) -> None:
    conv = conversations.get(conversation_id)
    if not conv:
        return
    if conv.get("title") and conv["title"] != "Nowa rozmowa":
        return
    hint = TITLE_HINTS.get(user_text.lower().strip())
    if hint:
        conv["title"] = hint
        return
    cleaned = " ".join(user_text.split())
    if cleaned.lower() in {"start", "hej", "cześć", "czesc", "menu", "restart"}:
        return
    if cleaned:
        conv["title"] = cleaned[:42]


def save_agent_state(conversation_id: str, agent_state: Optional[Dict]) -> None:
    conv = conversations.get(conversation_id)
    if not conv:
        return
    conv["agentState"] = agent_state
    persist_conversations()


load_conversations()
