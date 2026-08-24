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
- **API Key Security**: Admin endpoints (`GET /invitados`, `POST /invitados`, `DELETE`, `/invitados-view`) require the `API_KEY` configured in `project.env`.
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
| C | `nombre` | `string` | First name | `"Juan"` |
| D | `apellido` | `string` | Last name | `"Pérez"` |
| E | `telefono` | `string` | Phone number | `"+5491112345678"` |
| F | `invitacion_id` | `string` | Invitation note / group code | `"familia_perez"` |
| G | `cenaobaile` | `string` | Event type / pass | `"cena"` |
| H | `invitaoreserva` | `string` | Host or reserved | `"invitado"` |
| I | `precio` | `string` | Ticket price | `""` |
| J | `pago` | `string` | Payment status | `""` |
| K | `confirmacion` | `string` | Attendance confirmation (`"si"`, `"no"`, `""`) | `"si"` |
| L | `pa_general` | `string` | Standard diet flag | `"si"` |
| M | `pa_vegetariano` | `string` | Vegetarian diet flag | `"si"` |
| N | `pa_vegano` | `string` | Vegan diet flag | `"si"` |
| O | `pa_celiaco` | `string` | Celiac diet flag | `"si"` |
| P | `url` | `string` | Tamper-proof invitation URL with check code | `"http://nos.vamos.acas.ar/i/familia_perez_a1b2c3"` |
| Q | `whatsapp` | `string` | Direct WhatsApp invitation link (`wa.me`) | `"https://wa.me/5491112345678?text=http%3A%2F%2Fnos.vamos.acas.ar%2Fi%2Ffamilia_perez_a1b2c3"` |



> **Note**: There are **no mandatory fields**. Any payload field is optional. If an `id` is not specified on insert, a random 8-digit hex string is automatically generated.

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

## 🔒 4. Concurrency & Thread Safety

Google Sheets API operations are synchronized in Python using `threading.RLock()` inside [`GoogleSheetsTable`](file:///home/alfredo/dev/boda/api/app/sheets.py).

