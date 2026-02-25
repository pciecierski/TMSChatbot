# Transport Chatbot (FastAPI)

Szybki prototyp webowego chatbota do tworzenia zleceń transportowych.

## Uruchomienie lokalne
```bash
pip install -r requirements.txt
cd server
py -m uvicorn main:app --host 0.0.0.0 --port 8000
```
Otwórz http://localhost:8000 w przeglądarce.
Panel admina (lista zleceń): http://localhost:8000/admin

## API
- `POST /chat/message` – prosty kreator krok-po-kroku, pamięta sesję po `sessionId`.
- `GET /orders` – podgląd zapisanych zleceń (in-memory).
- `GET /orders/{id}` – szczegóły zlecenia.
- `GET /orders/{id}/public-link` – zwraca token i URL publicznego podglądu.
- `GET /view/{publicToken}` / `GET /public/orders/{publicToken}` – publiczny, tylko do odczytu widok zlecenia (dane + oferta + status akceptacji).
- `POST /orders/{id}/offer` – zapis oferty (cena, termin dostawy, kierowca) i powiadomienie do czatu.
- `GET /chat/notifications?sessionId=...` – pobiera nowe powiadomienia (np. o ofercie) dla sesji czatu; po złożeniu oferty klient dostaje pytanie o akceptację, a odpowiedź zapisuje status na karcie zlecenia.
- `GET /admin` – prosty widok listy wszystkich zleceń i formularz oferty.

## Trwałość danych
- Zlecenia i oferty są zapisywane do pliku `server/data/orders.json`. Po restarcie serwera dane są wczytywane automatycznie.
- Jeśli plik ulegnie uszkodzeniu, serwer wystartuje z pustym stanem (plik pozostanie do wglądu).

## Deploy na Railway (prosty)
1. W repo jest `railway.toml` z komendą startu: `cd server && uvicorn main:app --host 0.0.0.0 --port ${PORT}`.
2. W Railway utwórz projekt → Deploy from GitHub repo.
3. Build: Nixpacks wykryje `requirements.txt` (z root) i zainstaluje zależności.
4. Persistent data: w Railway dodaj Volume i zamontuj go w `/app/server/data`, by `orders.json` przetrwał restarty.
5. Po deploy otrzymasz publiczny URL (np. https://…railway.app). Front statyczny jest serwowany z tego samego procesu.

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
- Stan i zlecenia trzymane w pamięci procesu; do demo/prototype OK.
- Static front (HTML/JS/CSS) serwowany z FastAPI (`/`).
- Możesz zmienić pytania w `FIELDS` w `server/main.py` żeby dopasować do procesu.
