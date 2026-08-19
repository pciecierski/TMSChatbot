# Transport Chatbot (FastAPI)

Szybki prototyp webowego chatbota do tworzenia zleceń transportowych.

## Uruchomienie lokalne
```bash
pip install -r requirements.txt
cd server
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```
Otwórz http://localhost:8000 w przeglądarce.
Panel admina (lista zleceń): http://localhost:8000/admin

Hasło panelu admina pochodzi ze zmiennej `ADMIN_PASSWORD` (domyślnie `qqq` na potrzeby lokalnego podglądu). Logowanie ustawia ciasteczko sesji; lista zleceń i składanie ofert wymagają tej sesji.

## Testy
```bash
pip install -r requirements-dev.txt
pytest
```

## API
- `POST /chat/message` – kreator krok-po-kroku; przyjmuje `visitorId` + `conversationId` (albo legacy `sessionId`).
- `GET /chat/conversations?visitorId=` – lista wątków (bieżące i archiwalne).
- `POST /chat/conversations` – nowa rozmowa; poprzednia trafia do archiwum.
- `GET /chat/conversations/{id}?visitorId=` – historia wiadomości wątku.
- `POST /chat/conversations/{id}/reopen` – wznów archiwalny wątek.
- `GET /orders` – podgląd zapisanych zleceń (wymaga sesji admina).
- `GET /orders/{id}` – szczegóły zlecenia (wymaga sesji admina).
- `GET /orders/{id}/public-link` – zwraca token i URL publicznego podglądu (wymaga sesji admina).
- `GET /view/{publicToken}` / `GET /public/orders/{publicToken}` – publiczny, tylko do odczytu widok zlecenia (dane + oferta + status akceptacji).
- `POST /orders/{id}/offer` – zapis oferty (cena, termin dostawy, kierowca) i powiadomienie do czatu (wymaga sesji admina).
- `POST /admin/login` – logowanie do panelu (`{"password": "..."}`).
- `GET /admin/session` / `POST /admin/logout` – sprawdzenie i zakończenie sesji admina.
- `GET /chat/notifications?sessionId=...` – pobiera nowe powiadomienia (np. o ofercie) dla sesji czatu; po złożeniu oferty klient dostaje pytanie o akceptację, a odpowiedź zapisuje status na karcie zlecenia.
- `GET /yard-requests` / `POST /yard-requests/{id}/status` – zgłoszenia kierowców z terenu parku (wymaga sesji admina).

## Trwałość danych
- Zlecenia: `server/data/orders.json`.
- Zgłoszenia dyspozytora (park): `server/data/yard_requests.json`.
- Na Railway zamontuj Volume w `/app/server/data`, żeby oba pliki przetrwały restarty.
- Postgres **nie jest wymagany** na tym etapie. Przyda się, gdy będzie wielu równoległych użytkowników, kilka instancji aplikacji albo wspólna historia web + WhatsApp w większej skali. Wtedy wątki z JSON da się przenieść 1:1 (visitor, conversation, messages).

## Deploy na Railway (prosty)
1. W repo jest `railway.toml` z komendą startu: `cd server && uvicorn main:app --host 0.0.0.0 --port ${PORT}`.
2. W Railway utwórz projekt → Deploy from GitHub repo.
3. Build: Nixpacks wykryje `requirements.txt` (z root) i zainstaluje zależności.
4. Persistent data: w Railway dodaj Volume i zamontuj go w `/app/server/data`, by `orders.json`, `conversations.json` i `yard_requests.json` przetrwały restarty.
5. Ustaw zmienne:
   - `ADMIN_PASSWORD` – hasło panelu administratora.
   - `PUBLIC_BASE_URL` – publiczny URL aplikacji (np. `https://twoja-usluga.up.railway.app`), używany w linkach podglądu wysyłanych na czat/WhatsApp.
   - `COOKIE_SECURE=true` – gdy aplikacja działa na HTTPS.
6. Po deploy otrzymasz publiczny URL (np. https://…railway.app). Front statyczny jest serwowany z tego samego procesu.

## WhatsApp (Twilio)
- Webhook: `POST /webhook/whatsapp` – przyjmuje pola `Body`, `WaId`/`From` (formularz x-www-form-urlencoded z Twilio) i odsyła TwiML z odpowiedzią chatbota.
- Skonfiguruj w Twilio Sandbox lub WhatsApp Business API adres webhooka: `https://<twoj_host>/webhook/whatsapp`.
- Identyfikator sesji to `WaId` (numer użytkownika), więc rozmowa jest utrzymywana per numer.

## WhatsApp Cloud API (Meta)
- Weryfikacja webhooka: `GET /webhook/whatsapp/meta` z parametrami `hub.mode`, `hub.challenge`, `hub.verify_token`.
- Odbiór wiadomości: `POST /webhook/whatsapp/meta` (payload z Graph API). Obsługuje wiadomości tekstowe, sesja po numerze `from`.
- Odpowiedzi są wysyłane przez Graph API: `POST https://graph.facebook.com/v19.0/<PHONE_NUMBER_ID>/messages`.
- Wymagane zmienne środowiskowe:
  - `WHATSAPP_VERIFY_TOKEN` – Twój token do weryfikacji webhooka.
  - `WHATSAPP_TOKEN` – permanentny access token z Meta.
  - `WHATSAPP_PHONE_NUMBER_ID` – ID numeru WhatsApp (z konfiguracji Cloud API).

## Notatki
- Historia czatu jest zapisywana do `conversations.json`; stan agenta w wątku też, więc odświeżenie strony odtwarza dymki i krok rozmowy.
- Static front (HTML/JS/CSS) serwowany z FastAPI (`/`).
- Możesz zmienić pytania w `FIELDS` w `server/main.py` żeby dopasować do procesu.
