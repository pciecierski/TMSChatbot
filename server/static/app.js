const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chat-form");
const inputEl = document.getElementById("message-input");
const whatsappBtn = document.querySelector(".whatsapp-btn");
const newChatBtn = document.getElementById("new-chat-btn");
const activeListEl = document.getElementById("thread-list-active");
const archiveListEl = document.getElementById("thread-list-archive");

const API_BASE = "";
const NOTIF_INTERVAL_MS = 5000;

function generateId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function getVisitorId() {
  const stored = localStorage.getItem("chat-visitor-id") || localStorage.getItem("chat-session-id");
  if (stored) {
    localStorage.setItem("chat-visitor-id", stored);
    return stored;
  }
  const id = generateId();
  localStorage.setItem("chat-visitor-id", id);
  return id;
}

const visitorId = getVisitorId();
let conversationId = localStorage.getItem("chat-conversation-id") || "";

function setConversationId(id) {
  conversationId = id;
  if (id) localStorage.setItem("chat-conversation-id", id);
}

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

function setTyping(on) {
  const existing = messagesEl.querySelector(".bubble.typing");
  if (!on) {
    if (existing) existing.remove();
    return;
  }
  if (existing) return;
  const div = document.createElement("div");
  div.className = "bubble bot typing";
  div.textContent = "Agent pisze…";
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
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

function renderHistory(messages) {
  messagesEl.innerHTML = "";
  (messages || []).forEach((msg, index) => {
    addMessage(msg.text, msg.role === "user" ? "user" : "bot");
    const isLast = index === messages.length - 1;
    if (isLast && msg.role !== "user") {
      renderActionButtons(msg.buttons || []);
    }
  });
}

function threadButton(conv) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "thread-item" + (conv.id === conversationId ? " active" : "");
  const preview = document.createElement("span");
  preview.className = "thread-preview";
  preview.textContent = conv.preview || "Pusta rozmowa";
  btn.textContent = conv.title || "Nowa rozmowa";
  btn.appendChild(preview);
  btn.addEventListener("click", () => openConversation(conv.id, conv.archived));
  return btn;
}

async function refreshThreadList() {
  const res = await fetch(`${API_BASE}/chat/conversations?visitorId=${encodeURIComponent(visitorId)}`);
  if (!res.ok) return [];
  const data = await res.json();
  const items = data.conversations || [];
  const active = items.filter((item) => !item.archived);
  const archived = items.filter((item) => item.archived);
  activeListEl.innerHTML = "";
  archiveListEl.innerHTML = "";
  if (!active.length) {
    activeListEl.innerHTML = "<div class='thread-empty'>Brak bieżącej rozmowy.</div>";
  } else {
    active.forEach((conv) => activeListEl.appendChild(threadButton(conv)));
  }
  if (!archived.length) {
    archiveListEl.innerHTML = "<div class='thread-empty'>Brak archiwum.</div>";
  } else {
    archived.forEach((conv) => archiveListEl.appendChild(threadButton(conv)));
  }
  return items;
}

async function postChat(message, userDisplay) {
  const res = await fetch(`${API_BASE}/chat/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sessionId: conversationId || visitorId,
      visitorId,
      conversationId,
      message,
      userDisplay: userDisplay || message,
    }),
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
  setTyping(true);

  try {
    const data = await postChat(message, displayText);
    setTyping(false);
    if (data.conversationId) setConversationId(data.conversationId);
    addMessage(data.reply, "bot");
    renderActionButtons(data.buttons || []);
    setPlaceholder(data.nextField);
    await refreshThreadList();
  } catch (err) {
    setTyping(false);
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

async function startFreshConversation() {
  messagesEl.innerHTML = "";
  const res = await fetch(`${API_BASE}/chat/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ visitorId }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const conv = await res.json();
  setConversationId(conv.id);
  const data = await postChat("start");
  addMessage(data.reply, "bot");
  renderActionButtons(data.buttons || []);
  setPlaceholder(data.nextField);
  await refreshThreadList();
}

async function openConversation(id, archived) {
  if (archived) {
    await fetch(`${API_BASE}/chat/conversations/${id}/reopen`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visitorId }),
    });
  }
  const res = await fetch(
    `${API_BASE}/chat/conversations/${id}?visitorId=${encodeURIComponent(visitorId)}`
  );
  if (!res.ok) return;
  const conv = await res.json();
  setConversationId(conv.id);
  renderHistory(conv.messages || []);
  if (!(conv.messages || []).length) {
    const data = await postChat("start");
    addMessage(data.reply, "bot");
    renderActionButtons(data.buttons || []);
    setPlaceholder(data.nextField);
  }
  await refreshThreadList();
}

async function boot() {
  try {
    const items = await refreshThreadList();
    const current = items.find((item) => item.id === conversationId) || items.find((item) => !item.archived);
    if (current) {
      await openConversation(current.id, current.archived);
      return;
    }
    await startFreshConversation();
  } catch (err) {
    addMessage("Nie udało się połączyć z API.", "bot");
    console.error(err);
  }
}

async function pollNotifications() {
  if (!conversationId) return;
  try {
    const res = await fetch(
      `${API_BASE}/chat/notifications?sessionId=${encodeURIComponent(conversationId)}&conversationId=${encodeURIComponent(conversationId)}`
    );
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
    await refreshThreadList();
  } catch (err) {
    console.error("poll error", err);
  }
}

newChatBtn.addEventListener("click", async () => {
  try {
    await startFreshConversation();
  } catch (err) {
    addMessage("Nie udało się utworzyć nowej rozmowy.", "bot");
    console.error(err);
  }
});

boot();
pollNotifications();
setInterval(pollNotifications, NOTIF_INTERVAL_MS);

if (whatsappBtn) {
  const mobileHref = whatsappBtn.getAttribute("data-wa-mobile");
  const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
  if (mobileHref && isMobile) {
    whatsappBtn.href = mobileHref;
  }
}
