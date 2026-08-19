const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("message-input");
const whatsappBtn = document.querySelector(".whatsapp-btn");

const API_BASE = "";
const NOTIF_INTERVAL_MS = 5000;

function generateSessionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
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

function clearActionButtons() {
  messagesEl.querySelectorAll(".chat-actions").forEach((el) => el.remove());
}

function addMessage(text, sender = "bot") {
  const div = document.createElement("div");
  div.className = `bubble ${sender}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function renderActionButtons(buttons) {
  clearActionButtons();
  if (!buttons || !buttons.length) return;
  const wrap = document.createElement("div");
  wrap.className = "chat-actions";
  buttons.forEach((btn) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chat-action";
    button.textContent = btn.label;
    button.addEventListener("click", () => {
      sendMessage(btn.value, btn.label);
    });
    wrap.appendChild(button);
  });
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setPlaceholder(nextField) {
  const placeholders = {
    client_name: "np. ACME Logistics",
    pickup: "np. Warszawa, ul. Logistyczna 1",
    delivery: "np. Berlin, Hafenstraße 12",
    cargo: "np. 10 palet, 8 t",
    pickup_time: "np. 2026-08-21 08:00",
    contact: "np. +48 600 000 000",
    requirements: "np. winda, ADR albo „brak”",
    confirm: "tak albo nie",
    confirm_offer: "tak albo nie",
    confirm_delete: "tak albo nie",
    order_id: "numer z listy albo ID",
    field: "wybierz pole albo wpisz nazwę",
    choice: "napisz wiadomość albo wybierz akcję",
  };
  inputEl.placeholder = placeholders[nextField] || "Napisz wiadomość albo wybierz akcję...";
}

async function postChat(message) {
  const res = await fetch(`${API_BASE}/chat/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessionId, message }),
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.json();
}

async function sendMessage(message, displayText = message) {
  addMessage(displayText, "user");
  clearActionButtons();
  inputEl.value = "";
  inputEl.disabled = true;

  try {
    const data = await postChat(message);
    addMessage(data.reply, "bot");
    renderActionButtons(data.buttons || []);
    setPlaceholder(data.nextField);
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

async function startConversation() {
  try {
    const data = await postChat("start");
    addMessage(data.reply, "bot");
    renderActionButtons(data.buttons || []);
    setPlaceholder(data.nextField);
  } catch (err) {
    addMessage("Nie udało się połączyć z API.", "bot");
    console.error(err);
  }
}

async function pollNotifications() {
  try {
    const res = await fetch(`${API_BASE}/chat/notifications?sessionId=${encodeURIComponent(sessionId)}`);
    if (!res.ok) return;
    const data = await res.json();
    const messages = data.messages || [];
    if (!messages.length) return;
    messages.forEach((msg) => addMessage(msg, "bot"));
    renderActionButtons([
      { label: "Tak", value: "tak" },
      { label: "Nie", value: "nie" },
      { label: "Moje zlecenia", value: "lista" },
    ]);
    setPlaceholder("confirm_offer");
  } catch (err) {
    console.error("poll error", err);
  }
}

startConversation();
pollNotifications();
setInterval(pollNotifications, NOTIF_INTERVAL_MS);

if (whatsappBtn) {
  const mobileHref = whatsappBtn.getAttribute("data-wa-mobile");
  const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
  if (mobileHref && isMobile) {
    whatsappBtn.href = mobileHref;
  }
}
