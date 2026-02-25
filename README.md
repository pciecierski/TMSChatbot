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
- `POST /orders/{id}/offer` – zapis oferty (cena, termin dostawy, kierowca) i powiadomienie do czatu.
- `GET /chat/notifications?sessionId=...` – pobiera nowe powiadomienia (np. o ofercie) dla sesji czatu; po złożeniu oferty klient dostaje pytanie o akceptację, a odpowiedź zapisuje status na karcie zlecenia.
- `GET /admin` – prosty widok listy wszystkich zleceń i formularz oferty.

## Trwałość danych
- Zlecenia i oferty są zapisywane do pliku `server/data/orders.json`. Po restarcie serwera dane są wczytywane automatycznie.
- Jeśli plik ulegnie uszkodzeniu, serwer wystartuje z pustym stanem (plik pozostanie do wglądu).

## Notatki
- Stan i zlecenia trzymane w pamięci procesu; do demo/prototype OK.
- Static front (HTML/JS/CSS) serwowany z FastAPI (`/`).
- Możesz zmienić pytania w `FIELDS` w `server/main.py` żeby dopasować do procesu.
