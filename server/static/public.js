const API_BASE = "";

const orderView = document.getElementById("order-view");
const loadingEl = document.getElementById("loading");
const errorEl = document.getElementById("error");

const orderIdEl = document.getElementById("order-id");
const orderDateEl = document.getElementById("order-date");
const orderRouteEl = document.getElementById("order-route");
const orderClientEl = document.getElementById("order-client");
const orderContactEl = document.getElementById("order-contact");
const orderCargoEl = document.getElementById("order-cargo");
const orderPickupTimeEl = document.getElementById("order-pickup-time");
const orderReqEl = document.getElementById("order-req");
const offerBoxEl = document.getElementById("offer-box");

function getTokenFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  return parts[parts.length - 1] || null;
}

const publicToken = getTokenFromPath();

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("pl-PL");
}

function offerStatus(offer) {
  if (!offer) {
    return `<span class="badge warning">Oferta w przygotowaniu</span>`;
  }
  if (offer.accepted === true) {
    return `<span class="badge success">Oferta zaakceptowana</span>`;
  }
  if (offer.accepted === false) {
    return `<span class="badge danger">Oferta odrzucona</span>`;
  }
  return `<span class="badge warning">Oferta oczekuje na akceptację</span>`;
}

function renderOffer(offer) {
  if (!offer) {
    return `<p class="muted">Oferta nie została jeszcze przygotowana.</p>`;
  }
  return `
    <div class="order-meta">
      <span class="badge">Oferta</span>
      ${offerStatus(offer)}
    </div>
    <ul class="offer-details">
      <li><strong>Cena:</strong> ${offer.price}</li>
      <li><strong>Planowany termin dostawy:</strong> ${offer.eta}</li>
      <li><strong>Kierowca:</strong> ${offer.driver}</li>
    </ul>
  `;
}

function showError(message) {
  loadingEl.classList.add("hidden");
  orderView.classList.add("hidden");
  errorEl.textContent = message;
  errorEl.classList.remove("hidden");
}

function renderOrder(order) {
  orderIdEl.textContent = order.id;
  orderDateEl.textContent = formatDate(order.createdAt);
  orderRouteEl.textContent = `${order.data.pickup || "-"} → ${order.data.delivery || "-"}`;
  orderClientEl.textContent = order.data.client_name || "Brak nazwy zleceniodawcy";
  orderContactEl.textContent = order.data.contact || "-";
  orderCargoEl.textContent = order.data.cargo || "-";
  orderPickupTimeEl.textContent = order.data.pickup_time || "-";
  orderReqEl.textContent = order.data.requirements || "-";
  offerBoxEl.innerHTML = renderOffer(order.offer);

  loadingEl.classList.add("hidden");
  errorEl.classList.add("hidden");
  orderView.classList.remove("hidden");
}

async function loadOrder() {
  if (!publicToken) {
    showError("Brak tokenu w adresie linku.");
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/public/orders/${publicToken}`);
    if (res.status === 404) {
      showError("Nie znaleziono zlecenia dla tego linku.");
      return;
    }
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    renderOrder(data);
  } catch (err) {
    console.error(err);
    showError("Wystąpił błąd podczas ładowania zlecenia.");
  }
}

loadOrder();
