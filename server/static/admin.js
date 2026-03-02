const listEl = document.getElementById("orders-list");
const countEl = document.getElementById("orders-count");
const statusEl = document.getElementById("status-text");
const refreshBtn = document.getElementById("refresh-btn");
const guardEl = document.getElementById("admin-guard");
const guardForm = document.getElementById("admin-guard-form");
const guardInput = document.getElementById("admin-guard-input");
const guardCancel = document.getElementById("admin-guard-cancel");
const guardError = document.getElementById("admin-guard-error");

const API_BASE = "";

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
      <span><strong>Cena:</strong> ${offer.price}</span>
      <span><strong>Dostawa:</strong> ${offer.eta}</span>
      <span><strong>Kierowca:</strong> ${offer.driver}</span>
    </div>
  `;
}

function renderOrders(orders) {
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
            <div><span class="badge">ID</span> ${order.id}</div>
            <div class="muted">${formatDate(order.createdAt)}</div>
          </div>
          <div class="order-meta">${statusBadge(order.status)}</div>
          <div class="order-route">${d.pickup || "-"} → ${d.delivery || "-"}</div>
          <div class="order-meta">
            <strong>${d.client_name || "Brak nazwy zleceniodawcy"}</strong>
          </div>
          ${offerSnippet(offer)}
          <div class="share-row">
            <input type="text" value="${shareUrl}" readonly />
            <button type="button" class="button secondary copy-link" data-link="${shareUrl}">Kopiuj link podglądu</button>
          </div>
          <details class="order-details">
            <summary>Szczegóły</summary>
            <ul>
              <li><strong>WhatsApp:</strong> ${d.whatsapp || "-"}</li>
              <li><strong>Kontakt:</strong> ${d.contact || "-"}</li>
              <li><strong>Ładunek:</strong> ${d.cargo || "-"}</li>
              <li><strong>Termin załadunku:</strong> ${d.pickup_time || "-"}</li>
              <li><strong>Wymagania:</strong> ${d.requirements || "-"}</li>
            </ul>
          </details>
          <form class="offer-form" data-id="${order.id}" data-cancelled="${isCancelled}">
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
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        statusSpan.textContent = "Oferta zapisana i wysłana do klienta.";
        await loadOrders(); // refresh to show offer
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

async function loadOrders() {
  statusEl.textContent = "Ładowanie...";
  refreshBtn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/orders`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const orders = Object.values(data || {}).sort(
      (a, b) => new Date(b.createdAt) - new Date(a.createdAt)
    );
    renderOrders(orders);
    statusEl.textContent = `Ostatnie odświeżenie: ${new Date().toLocaleTimeString("pl-PL")}`;
  } catch (err) {
    console.error(err);
    statusEl.textContent = "Błąd podczas ładowania listy.";
    listEl.innerHTML = "<p class='empty'>Nie udało się pobrać zleceń.</p>";
  } finally {
    refreshBtn.disabled = false;
  }
}

refreshBtn.addEventListener("click", loadOrders);

loadOrders();

function openGuard() {
  if (!guardEl) return;
  guardEl.classList.remove("hidden");
  document.body.classList.add("modal-open");
  guardError.textContent = "";
  guardInput.value = "";
  setTimeout(() => guardInput.focus(), 0);
}

function closeGuard() {
  if (!guardEl) return;
  guardEl.classList.add("hidden");
  document.body.classList.remove("modal-open");
}

if (guardEl && guardForm && guardInput && guardCancel) {
  openGuard();

  guardForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const value = guardInput.value.trim();
    if (value === "qqq") {
      closeGuard();
      if (guardEl && typeof guardEl.remove === "function") {
        guardEl.remove(); // usuń całkiem, by nie blokowało niczego nawet przy błędnym stylu/cache
      }
    } else {
      guardError.textContent = "Nieprawidłowe hasło.";
      guardInput.focus();
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
