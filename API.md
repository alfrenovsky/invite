# 📖 Boda CRUD API Documentation

RESTful CRUD API documentation for the **Boda** web application.

---

## 📌 1. Overview & Architecture

The **Boda API** is a Flask-based RESTful service that provides CRUD management for guest records (`invitados`). All records are synchronized in real-time with a Google Sheets spreadsheet via `gspread`.

```
                      ┌──────────────────────┐
                      │    Nginx Proxy       │
                      │   (Container: web)   │
                      └──────────┬───────────┘
                                 │
                ┌────────────────┴────────────────┐
                ▼                                 ▼
       Static Files (HTML)               Flask REST API
       (/usr/share/nginx/html)          (Container: api)
                                                  │
                                                  ▼
                                         Google Sheets API
                                        (gspread integration)
```

- **Base URL (Local/Docker)**: `http://localhost/` (via Nginx proxy) or `http://localhost:5000/` (Direct Flask container)
- **Content Type**: `application/json`
- **Data Persistence**: Google Sheets (Worksheet: `Confirmaciones` or configured via `WORKSHEET_NAME`)
- **API Key Security**: Admin endpoints (`GET /invitados`, `POST /invitados`, `DELETE`) require the `API_KEY` configured in `project.env`.
  - Pass via Header: `X-API-Key: <key>`
  - Pass via Bearer: `Authorization: Bearer <key>`
  - Pass via Query Param: `?api_key=<key>`
- **Rate Limiting**: Nginx rate-limits API requests to 20 req/s with a burst of 30.

---

## 📊 2. Data Models & Schemas


### 2.1 Google Sheets Column Headers (`FIELDNAMES`)

The sheet model strictly respects the following column order defined in `FIELDNAMES`:

| Column Index | Field Name | Type | Description | Example |
|---|---|---|---|---|
| A | `id` | `string` | Unique identifier (Deterministic 8-digit HEX) | `"a1b2c3d4"` |
| B | `updated_at` | `string` | Timestamp of insertion/last update | `"2026-08-11 18:30:00"` |
| C | `invitacion_id` | `string` | Invitation group slug / token | `"familia_perez"` |
| D | `nombre` | `string` | First name | `"Juan"` |
| E | `apellido` | `string` | Last name | `"Pérez"` |
| F | `telefono` | `string` | Phone number (immediately after apellido) | `"+5491112345678"` |
| G | `dequien` | `string` | Host affiliation | `"novia"` / `"novio"` |
| H | `tipo` | `string` | Guest type / category | `"amigo"` |
| I | `nivel` | `string` | Guest category (`"Reserva"`, `"Invitado"`) | `"Reserva"` |
| J | `confirmacion` | `string` | Attendance confirmation (`"si"`, `"no"`, `""`) | `"si"` |
| K | `montotarjeta` | `string` | Ticket/card amount (defaults to `100000` for `"Reserva"` if empty) | `"100000"` |
| L | `pa` | `string` | Main dietary preference (`"general"`, `"vegetariano"`, `"vegano"`) | `"general"` |
| M | `celiaco` | `string` | Celiac / Gluten-Free flag (`"si"`, `""`) | `"si"` |
| N | `url` | `string` | Tamper-proof invitation URL with check code | `"http://nos.vamos.acas.ar/familia_perez_a1b2c3"` |
| O | `enviado` | `string` | Invitation sent via WhatsApp flag (`"si"`, `""`) | `"si"` |
| P | `abierto` | `string` | Invitation opened by a human (`"si"`, `""`) | `"si"` |
| Q | `pagado` | `string` | Payment received verification (`"si"`, `""`) | `"si"` |
| R | `comprobante` | `string` | Uploaded transfer receipt filename/URL | `"comprobante_a1b2c3d4.jpg"` |




> **Note**: There are **no mandatory fields**. Any payload field is optional. If an `id` is not specified on insert, a random 8-digit hex string is automatically generated. The direct WhatsApp URL (`whatsapp`) is computed and preserved internally in API responses for quick sharing, but the Google Sheet column stores only the `enviado` status (`"si"` or `""`).

---

## 🚀 3. CRUD Endpoints Reference

### 3.1 Healthcheck

#### `GET /health`
Checks the operational health of the REST API service.

##### Response (`200 OK`)
```json
{
  "status": "ok"
}
```

---

### 3.2 List All Guests (`READ`)

#### `GET /invitados`
Retrieves all registered guest records from the Google Sheet database.

##### Response (`200 OK`)
```json
{
  "ok": true,
  "count": 1,
  "data": [
    {
      "id": "a1b2c3d4",
      "updated_at": "2026-08-11 18:30:00",
      "apellido": "Pérez",
      "nombre": "Juan",
      "telefono": "+5491112345678",
      "invitacion": "VIP",
      "confirmacion": "si",
      "pa_general": "si",
      "pa_vegetariano": "",
      "pa_vegano": "",
      "pa_celiaco": "",
      "": ""
    }
  ]
}
```

##### Example Curl Request
```bash
curl -X GET http://localhost/invitados \
  -H "X-API-Key: boda_secret_api_key_2027"
```


---

### 3.3 Get Guest by ID (`READ`)

#### `GET /invitados/<record_id>`
Retrieves details of a specific guest record by its unique ID.

- **Path Parameters**:
  - `record_id` (`string`): ID of the guest record.

##### Response (`200 OK`)
```json
{
  "ok": true,
  "data": {
    "id": "a1b2c3d4",
    "updated_at": "2026-08-11 18:30:00",
    "apellido": "Pérez",
    "nombre": "Juan",
    "confirmacion": "si"
  }
}
```

##### Error Responses
- `404 Not Found`:
  ```json
  { "ok": false, "error": "Invitado no encontrado" }
  ```

##### Example Curl Request
```bash
curl -X GET http://localhost/invitados/a1b2c3d4
```

---

### 3.4 Create Guest Record (`CREATE`)

#### `POST /invitados`
Adds one or multiple new guest records directly to the system. No fields are mandatory.

- **Headers**: `Content-Type: application/json`
- **Request Body**: Single JSON object OR array of JSON objects.

##### Single Record Request Example
```json
{
  "nombre": "Carlos",
  "apellido": "López",
  "confirmacion": "si",
  "pa_celiaco": "si"
}
```

##### Batch Array Request Example
```json
[
  { "nombre": "Juan", "apellido": "Pérez", "confirmacion": "si" },
  { "nombre": "María", "apellido": "Gómez", "confirmacion": "no" }
]
```

##### Response (`201 Created`)
```json
{
  "ok": true,
  "data": {
    "id": "f9e8d7c6",
    "updated_at": "2026-08-11 18:35:00",
    "apellido": "López",
    "nombre": "Carlos",
    "telefono": "",
    "invitacion": "",
    "confirmacion": "si",
    "pa_general": "",
    "pa_vegetariano": "",
    "pa_vegano": "",
    "pa_celiaco": "si",
    "": ""
  }
}
```

##### Error Responses
- `400 Bad Request`:
  ```json
  { "ok": false, "error": "JSON inválido" }
  ```

##### Example Curl Request
```bash
curl -X POST http://localhost/invitados \
  -H "X-API-Key: boda_secret_api_key_2027" \
  -H "Content-Type: application/json" \
  -d '{ "nombre": "Carlos", "apellido": "López", "confirmacion": "si" }'
```


---

### 3.5 Update Guest Record (`UPDATE`)

#### `PUT /invitados/<record_id>` / `PATCH /invitados/<record_id>`
Updates fields of an existing guest record by ID.

- **Path Parameters**:
  - `record_id` (`string`): ID of the record to update.
- **Headers**: `Content-Type: application/json`

##### Request Example
```json
{
  "confirmacion": "no",
  "pa_vegano": "si"
}
```

##### Response (`200 OK`)
```json
{
  "ok": true,
  "data": {
    "id": "a1b2c3d4",
    "updated_at": "2026-08-11 18:40:00",
    "confirmacion": "no",
    "pa_vegano": "si"
  }
}
```

##### Error Responses
- `404 Not Found`:
  ```json
  { "ok": false, "error": "Invitado no encontrado" }
  ```

##### Example Curl Request
```bash
curl -X PATCH http://localhost/invitados/a1b2c3d4 \
  -H "Content-Type: application/json" \
  -d '{ "confirmacion": "no" }'
```

---

### 3.6 Delete Guest Record (`DELETE`)

#### `DELETE /invitados/<record_id>`
Deletes a guest record by ID from Google Sheets.

- **Path Parameters**:
  - `record_id` (`string`): ID of record to delete.

##### Response (`200 OK`)
```json
{
  "ok": true,
  "message": "Invitado eliminado correctamente"
}
```

##### Error Responses
- `404 Not Found`:
  ```json
  { "ok": false, "error": "Invitado no encontrado" }
  ```

##### Example Curl Request
```bash
curl -X DELETE http://localhost/invitados/a1b2c3d4 \
  -H "X-API-Key: boda_secret_api_key_2027"
```

---

### 3.7 Ensure IDs for All Guests (`MAINTENANCE`)

#### `POST /invitados/ensure-ids`
Scans all guest rows in Google Sheets. Any row with a blank or missing `id` will automatically be assigned a new 8-character hex ID and updated in Google Sheets. *(Note: `GET /invitados` also automatically triggers this check).*

##### Response (`200 OK`)
```json
{
  "ok": true,
  "data": [ ... ],
  "updated_count": 2
}
```

##### Example Curl Request
```bash
curl -X POST http://localhost/invitados/ensure-ids \
  -H "X-API-Key: boda_secret_api_key_2027"
```


---

### 3.8 Dynamic Slides Manifest (`READ`)

#### `GET /i/<token>/slides`
Returns JSON array of currently enabled slide definitions for the given invitation token.

##### Response (`200 OK`)
```json
{
  "ok": true,
  "invitacion_id": "familia_perez",
  "count": 6,
  "slides": [
    {
      "id": "intro",
      "title": "Portada",
      "order": 1,
      "duration": 7000,
      "url": "/i/familia_perez_a1b2c3/slide/intro"
    },
    {
      "id": "rsvp",
      "title": "Confirmación",
      "order": 6,
      "duration": 0,
      "url": "/i/familia_perez_a1b2c3/slide/rsvp"
    }
  ]
}
```

---

### 3.9 Render Single Slide HTML (`READ`)

#### `GET /i/<token>/slide/<slide_id>`
Renders and returns the HTML snippet for a specific slide ID (`intro`, `fecha_lugar`, `lugar`, `itinerario`, `regalos`, `rsvp`).

##### Response (`200 OK`)
```html
<div class="story-slide active" data-duration="7000">
    <div class="slide-body">
        ...
    </div>
</div>
```

---

### 3.10 Send Redirect to WhatsApp (`DISPATCH`)

#### `GET /send/<phone>`
Marks the recipient individual record as sent (`enviado="si"`) in Google Sheets / local cache, then immediately issues a `302 Found` HTTP redirect to their direct WhatsApp chat (`https://wa.me/<phone>?text=...`). Social crawler bots are automatically ignored to avoid false sent flags. An optional `?id=<guest_id>` query param can be passed to disambiguate guests who share the exact same phone number.

##### Response (`302 Found`)
- Header: `Location: https://wa.me/5491112345678?text=...`

---

## 🔒 4. Concurrency & Thread Safety

Google Sheets API operations are synchronized in Python using `threading.RLock()` inside [`GoogleSheetsTable`](file:///home/alfredo/dev/boda/api/app/sheets.py).


