const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("message-input");

const API_BASE = "";
const NOTIF_INTERVAL_MS = 5000;

function generateSessionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback dla starszych przeglądarek
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function getSessionId() {
  const stored = localStorage.getItem("chat-session-id");
  if (stored) return stored;
  const id = generateSessionId();
  localStorage.setItem("chat-session-id", id);
  return id;
}

const sessionId = getSessionId();

function addMessage(text, sender = "bot") {
  const div = document.createElement("div");
  div.className = `bubble ${sender}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function sendMessage(message) {
  addMessage(message, "user");
  inputEl.value = "";
  inputEl.disabled = true;

  try {
    const res = await fetch(`${API_BASE}/chat/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, message }),
    });

    if (!res.ok) {
      addMessage("Błąd serwera, spróbuj ponownie.", "bot");
      return;
    }

    const data = await res.json();
    addMessage(data.reply, "bot");
  } catch (err) {
    addMessage("Nie udało się połączyć z API.", "bot");
    console.error(err);
  } finally {
    inputEl.disabled = false;
    inputEl.focus();
  }
}

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  const message = inputEl.value.trim();
  if (!message) return;
  sendMessage(message);
});

// Start conversation (bez wysyłania "start" od użytkownika)
addMessage("Cześć! Pomogę Ci stworzyć zlecenie transportowe.");
addMessage("Co chcesz zrobić? wpisz: 'nowe', 'edytuj' lub 'lista'.");

async function pollNotifications() {
  try {
    const res = await fetch(`${API_BASE}/chat/notifications?sessionId=${encodeURIComponent(sessionId)}`);
    if (!res.ok) return;
    const data = await res.json();
    (data.messages || []).forEach((msg) => addMessage(msg, "bot"));
  } catch (err) {
    console.error("poll error", err);
  }
}

pollNotifications();
setInterval(pollNotifications, NOTIF_INTERVAL_MS);
