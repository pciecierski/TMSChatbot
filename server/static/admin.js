const listEl = document.getElementById("orders-list");
const yardListEl = document.getElementById("yard-list");
const yardCountEl = document.getElementById("yard-count");
const visitsListEl = document.getElementById("visits-list");
const visitsCountEl = document.getElementById("visits-count");
const countEl = document.getElementById("orders-count");
const statusEl = document.getElementById("status-text");
const refreshBtn = document.getElementById("refresh-btn");
const logoutBtn = document.getElementById("logout-btn");
const guardEl = document.getElementById("admin-guard");
const guardForm = document.getElementById("admin-guard-form");
const guardInput = document.getElementById("admin-guard-input");
const guardCancel = document.getElementById("admin-guard-cancel");
const guardError = document.getElementById("admin-guard-error");

const API_BASE = "";
const LIVE_POLL_MS = 4000;

let liveRevision = 0;
let liveTimer = null;
let loading = false;
let cachedOrders = [];

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("pl-PL");
}

function statusBadge(status) {
  const norm = (status || "").toLowerCase();
  if (norm === "anulowane") return "<span class='badge danger'>Anulowane</span>";
  return "<span class='badge success'>Aktywne</span>";
}

function offerSnippet(offer) {
  if (!offer) return "<span class='muted'>Brak oferty</span>";
  let status = "<span class='badge warning'>Oczekuje akceptacji</span>";
  if (offer.accepted === true) status = "<span class='badge success'>Zaakceptowana</span>";
  if (offer.accepted === false) status = "<span class='badge danger'>Odrzucona</span>";
  return `
    <div class="order-meta">
      <span class="badge">Oferta</span>
      ${status}
      <span><strong>Cena:</strong> ${escapeHtml(offer.price)}</span>
      <span><strong>Dostawa:</strong> ${escapeHtml(offer.eta)}</span>
      <span><strong>Kierowca:</strong> ${escapeHtml(offer.driver)}</span>
    </div>
  `;
}

function orderOptionsHtml(selectedId) {
  const options = ['<option value="">— brak powiązania —</option>'];
  cachedOrders.forEach((order) => {
    const d = order.data || {};
    const label = `${order.id} · ${d.client_name || "bez nazwy"} · ${d.pickup || "?"} → ${d.delivery || "?"}`;
    const selected = order.id === selectedId ? " selected" : "";
    options.push(`<option value="${escapeHtml(order.id)}"${selected}>${escapeHtml(label)}</option>`);
  });
  return options.join("");
}

function renderOrders(orders) {
  cachedOrders = orders;
  countEl.textContent = orders.length;

  if (!orders.length) {
    listEl.innerHTML = "<p class='empty'>Brak zleceń.</p>";
    return;
  }

  const html = orders
    .map((order) => {
      const d = order.data || {};
      const offer = order.offer;
      const shareUrl = `${window.location.origin}/view/${order.publicToken}`;
      const isCancelled = (order.status || "").toLowerCase() === "anulowane";
      const disabledAttr = isCancelled ? "disabled" : "";
      return `
        <div class="order-card">
          <div class="order-header">
            <div><span class="badge">ID</span> ${escapeHtml(order.id)}</div>
            <div class="muted">${escapeHtml(formatDate(order.createdAt))}</div>
          </div>
          <div class="order-meta">${statusBadge(order.status)}</div>
          <div class="order-route">${escapeHtml(d.pickup || "-")} → ${escapeHtml(d.delivery || "-")}</div>
          <div class="order-meta">
            <strong>${escapeHtml(d.client_name || "Brak nazwy zleceniodawcy")}</strong>
          </div>
          ${offerSnippet(offer)}
          <div class="share-row">
            <input type="text" value="${escapeHtml(shareUrl)}" readonly />
            <button type="button" class="button secondary copy-link" data-link="${escapeHtml(shareUrl)}">Kopiuj link podglądu</button>
          </div>
          <details class="order-details">
            <summary>Szczegóły</summary>
            <ul>
              <li><strong>WhatsApp:</strong> ${escapeHtml(d.whatsapp || "-")}</li>
              <li><strong>Kontakt:</strong> ${escapeHtml(d.contact || "-")}</li>
              <li><strong>Ładunek:</strong> ${escapeHtml(d.cargo || "-")}</li>
              <li><strong>Termin załadunku:</strong> ${escapeHtml(d.pickup_time || "-")}</li>
              <li><strong>Wymagania:</strong> ${escapeHtml(d.requirements || "-")}</li>
            </ul>
          </details>
          <form class="offer-form" data-id="${escapeHtml(order.id)}" data-cancelled="${isCancelled}">
            <div class="offer-grid">
              <label>
                Cena
                <input type="text" name="price" placeholder="np. 2500 PLN" required ${disabledAttr}/>
              </label>
              <label>
                Termin dostawy
                <input type="text" name="eta" placeholder="np. 2026-03-01 10:00" required ${disabledAttr}/>
              </label>
              <label>
                Kierowca
                <input type="text" name="driver" placeholder="Imię i nazwisko" required ${disabledAttr}/>
              </label>
            </div>
            <div class="offer-actions">
              <button type="submit" ${disabledAttr}>Złóż ofertę</button>
              <span class="offer-status muted">${isCancelled ? "Zlecenie anulowane" : ""}</span>
            </div>
          </form>
        </div>
      `;
    })
    .join("");

  listEl.innerHTML = html;

  listEl.querySelectorAll(".offer-form").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const orderId = form.dataset.id;
      const isCancelled = form.dataset.cancelled === "true";
      if (isCancelled) return;
      const statusSpan = form.querySelector(".offer-status");
      statusSpan.textContent = "Wysyłam...";

      const body = {
        price: form.price.value.trim(),
        eta: form.eta.value.trim(),
        driver: form.driver.value.trim(),
      };

      try {
        const res = await fetch(`${API_BASE}/orders/${orderId}/offer`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify(body),
        });
        if (res.status === 401) {
          openGuard();
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        statusSpan.textContent = "Oferta zapisana i wysłana do klienta.";
        await loadOrders();
      } catch (err) {
        console.error(err);
        statusSpan.textContent = "Błąd zapisu oferty.";
      }
    });
  });

  listEl.querySelectorAll(".copy-link").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const link = btn.dataset.link;
      try {
        await navigator.clipboard.writeText(link);
        const original = btn.textContent;
        btn.textContent = "Skopiowano";
        setTimeout(() => {
          btn.textContent = original;
        }, 1500);
      } catch (err) {
        console.error(err);
        btn.textContent = "Kopiowanie nieudane";
      }
    });
  });
}

function yardStatusBadge(status) {
  const norm = (status || "").toLowerCase();
  if (norm === "przyjęte") return "<span class='badge success'>Przyjęte</span>";
  if (norm === "odrzucone") return "<span class='badge danger'>Odrzucone</span>";
  return "<span class='badge warning'>Oczekuje</span>";
}

function renderYardRequests(items) {
  if (!yardListEl || !yardCountEl) return;
  yardCountEl.textContent = items.length;
  if (!items.length) {
    yardListEl.innerHTML = "<p class='empty'>Brak zgłoszeń z parku.</p>";
    return;
  }
  yardListEl.innerHTML = items
    .map((item) => {
      const d = item.data || {};
      const extra = d.requested_time
        ? `<li><strong>Nowy termin:</strong> ${escapeHtml(d.requested_time)}</li>`
        : d.pause_until
          ? `<li><strong>Postój:</strong> ${escapeHtml(d.pause_until)}</li>`
          : d.trailer_pickup_at
            ? `<li><strong>Odbiór naczepy:</strong> ${escapeHtml(d.trailer_pickup_at)}</li>`
            : "";
      return `
        <div class="order-card">
          <div class="order-header">
            <div><span class="badge">ID</span> ${escapeHtml(item.id)}</div>
            <div class="muted">${escapeHtml(formatDate(item.createdAt))}</div>
          </div>
          <div class="order-meta">${yardStatusBadge(item.status)}</div>
          <div class="order-route">${escapeHtml(item.kindLabel || item.kind)}</div>
          <div class="order-meta"><strong>${escapeHtml(d.driver_name || "Kierowca")}</strong></div>
          <details class="order-details">
            <summary>Szczegóły</summary>
            <ul>
              <li><strong>Pojazd / naczepa:</strong> ${escapeHtml(d.plates || "-")}</li>
              ${extra}
            </ul>
          </details>
          <div class="offer-actions">
            <button type="button" class="yard-status" data-id="${escapeHtml(item.id)}" data-status="Przyjęte">Przyjmij</button>
            <button type="button" class="button secondary yard-status" data-id="${escapeHtml(item.id)}" data-status="Odrzucone">Odrzuć</button>
          </div>
        </div>
      `;
    })
    .join("");

  yardListEl.querySelectorAll(".yard-status").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const res = await fetch(`${API_BASE}/yard-requests/${btn.dataset.id}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ status: btn.dataset.status }),
        });
        if (res.status === 401) {
          openGuard();
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await loadOrders();
      } catch (err) {
        console.error(err);
      }
    });
  });
}

async function loadYardRequests() {
  if (!yardListEl) return true;
  const res = await fetch(`${API_BASE}/yard-requests`, { credentials: "same-origin" });
  if (res.status === 401) {
    openGuard();
    return false;
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  const items = Object.values(data || {}).sort(
    (a, b) => new Date(b.createdAt) - new Date(a.createdAt)
  );
  renderYardRequests(items);
  return true;
}

const VISIT_STAGE_LABELS = {
  rozpoczeta: "wizyta rozpoczęta",
  dokumenty: "potwierdzone dokumenty",
  dok: "przypisany dok i przekazane do realizacji",
  zaladunek: "zakończony załadunek/rozładunek",
  dokumenty_wyjazd: "przygotowane dokumenty",
};

function visitStageLabel(stage) {
  return VISIT_STAGE_LABELS[stage] || stage || "-";
}

function linkedOrderLabel(orderId) {
  if (!orderId) return "Brak powiązania ze zleceniem";
  const order = cachedOrders.find((item) => item.id === orderId);
  if (!order) return `Zlecenie ${orderId} (niedostępne)`;
  const d = order.data || {};
  return `${order.id} · ${d.client_name || "bez nazwy"}`;
}

function renderVisits(items) {
  if (!visitsListEl || !visitsCountEl) return;
  visitsCountEl.textContent = items.length;
  if (!items.length) {
    visitsListEl.innerHTML = "<p class='empty'>Brak wizyt na placu.</p>";
    return;
  }
  visitsListEl.innerHTML = items
    .map((item) => {
      return `
        <div class="order-card">
          <div class="order-header">
            <div><span class="badge">ID</span> ${escapeHtml(item.id)}</div>
            <div class="muted">${escapeHtml(formatDate(item.updatedAt))}</div>
          </div>
          <div class="order-meta"><span class="badge warning">${escapeHtml(visitStageLabel(item.stage))}</span></div>
          <div class="order-meta"><strong>${escapeHtml(item.driver_name || "Kierowca")}</strong></div>
          <div class="order-route">${escapeHtml(item.plates || "-")}</div>
          <div class="order-meta muted">${escapeHtml(linkedOrderLabel(item.orderId))}${item.dock ? ` · ${escapeHtml(item.dock)}` : ""}</div>
          <form class="visit-link-form" data-id="${escapeHtml(item.id)}">
            <div class="offer-grid">
              <label>
                Zlecenie / awizacja
                <select name="orderId">${orderOptionsHtml(item.orderId || "")}</select>
              </label>
              <label>
                Dok
                <input type="text" name="dock" value="${escapeHtml(item.dock || "")}" placeholder="np. Dok 3" />
              </label>
            </div>
            <div class="offer-actions">
              <button type="submit" class="button secondary">Zapisz powiązanie</button>
              <button type="button" class="visit-advance" data-id="${escapeHtml(item.id)}">Następny etap</button>
              <span class="offer-status muted visit-link-status"></span>
            </div>
          </form>
        </div>
      `;
    })
    .join("");

  visitsListEl.querySelectorAll(".visit-advance").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const res = await fetch(`${API_BASE}/visits/${btn.dataset.id}/stage`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ advance: true }),
        });
        if (res.status === 401) {
          openGuard();
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await loadOrders();
      } catch (err) {
        console.error(err);
      }
    });
  });

  visitsListEl.querySelectorAll(".visit-link-form").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const statusSpan = form.querySelector(".visit-link-status");
      statusSpan.textContent = "Zapisuję...";
      try {
        const res = await fetch(`${API_BASE}/visits/${form.dataset.id}/link`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({
            orderId: form.orderId.value,
            dock: form.dock.value.trim(),
          }),
        });
        if (res.status === 401) {
          openGuard();
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        statusSpan.textContent = "Zapisano.";
        await loadOrders();
      } catch (err) {
        console.error(err);
        statusSpan.textContent = "Błąd zapisu.";
      }
    });
  });
}

async function loadVisits() {
  if (!visitsListEl) return true;
  const res = await fetch(`${API_BASE}/visits`, { credentials: "same-origin" });
  if (res.status === 401) {
    openGuard();
    return false;
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  const items = Object.values(data || {}).sort(
    (a, b) => new Date(b.updatedAt || 0) - new Date(a.updatedAt || 0)
  );
  renderVisits(items);
  return true;
}

async function loadOrders() {
  if (loading) return false;
  loading = true;
  statusEl.textContent = "Ładowanie...";
  refreshBtn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/orders`, { credentials: "same-origin" });
    if (res.status === 401) {
      openGuard();
      stopLivePoll();
      statusEl.textContent = "Wymagane logowanie.";
      return false;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const orders = Object.values(data || {}).sort(
      (a, b) => new Date(b.createdAt) - new Date(a.createdAt)
    );
    renderOrders(orders);
    await loadYardRequests();
    await loadVisits();
    await syncLiveRevision();
    statusEl.textContent = `Live · ostatnie odświeżenie: ${new Date().toLocaleTimeString("pl-PL")}`;
    startLivePoll();
    return true;
  } catch (err) {
    console.error(err);
    statusEl.textContent = "Błąd podczas ładowania listy.";
    listEl.innerHTML = "<p class='empty'>Nie udało się pobrać zleceń.</p>";
    return false;
  } finally {
    loading = false;
    refreshBtn.disabled = false;
  }
}

async function syncLiveRevision() {
  try {
    const res = await fetch(`${API_BASE}/admin/feed?since=0`, { credentials: "same-origin" });
    if (!res.ok) return;
    const data = await res.json();
    liveRevision = data.revision || 0;
  } catch (err) {
    console.error("sync revision", err);
  }
}

async function pollAdminFeed() {
  if (document.hidden || loading) return;
  try {
    const res = await fetch(`${API_BASE}/admin/feed?since=${liveRevision}`, {
      credentials: "same-origin",
    });
    if (res.status === 401) {
      openGuard();
      stopLivePoll();
      return;
    }
    if (!res.ok) return;
    const data = await res.json();
    const nextRevision = data.revision || liveRevision;
    const shouldReload = Boolean(data.changed) && nextRevision > liveRevision;
    liveRevision = nextRevision;
    if (shouldReload) {
      await loadOrders();
    }
  } catch (err) {
    console.error("admin feed", err);
  }
}

function startLivePoll() {
  if (liveTimer) return;
  liveTimer = setInterval(pollAdminFeed, LIVE_POLL_MS);
}

function stopLivePoll() {
  if (!liveTimer) return;
  clearInterval(liveTimer);
  liveTimer = null;
}

refreshBtn.addEventListener("click", loadOrders);

if (logoutBtn) {
  logoutBtn.addEventListener("click", async () => {
    stopLivePoll();
    try {
      await fetch(`${API_BASE}/admin/logout`, { method: "POST", credentials: "same-origin" });
    } catch (err) {
      console.error(err);
    }
    window.location.href = "/";
  });
}

function openGuard() {
  if (!guardEl) return;
  stopLivePoll();
  guardEl.classList.remove("hidden");
  guardEl.removeAttribute("hidden");
  guardEl.style.display = "flex";
  document.body.classList.add("modal-open");
  if (guardError) guardError.textContent = "";
  if (guardInput) guardInput.value = "";
  setTimeout(() => guardInput && guardInput.focus(), 0);
}

function closeGuard() {
  if (!guardEl) return;
  guardEl.classList.add("hidden");
  guardEl.setAttribute("hidden", "");
  guardEl.style.display = "none";
  document.body.classList.remove("modal-open");
}

async function checkSession() {
  try {
    const res = await fetch(`${API_BASE}/admin/session`, { credentials: "same-origin" });
    return res.ok;
  } catch (err) {
    console.error(err);
    return false;
  }
}

async function initAdmin() {
  const authenticated = await checkSession();
  if (authenticated) {
    closeGuard();
    await loadOrders();
    return;
  }
  openGuard();
}

if (guardEl && guardForm && guardInput && guardCancel) {
  guardForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const value = guardInput.value.trim();
    guardError.textContent = "";
    try {
      const res = await fetch(`${API_BASE}/admin/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ password: value }),
      });
      if (!res.ok) {
        guardError.textContent = "Nieprawidłowe hasło.";
        guardInput.focus();
        return;
      }
      closeGuard();
      await loadOrders();
    } catch (err) {
      console.error(err);
      guardError.textContent = "Nie udało się zalogować.";
    }
  });

  guardCancel.addEventListener("click", (e) => {
    e.preventDefault();
    window.location.href = "/";
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !guardEl.classList.contains("hidden")) {
      window.location.href = "/";
    }
  });
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    pollAdminFeed();
  }
});

initAdmin();
