# AGENTS.md

Welcome to the **Boda** project repo! This document provides an overview of the codebase architecture, tech stack, API specs, and development workflows for AI agents working on this project.

---

## 📌 Project Overview

**Boda** is a wedding guest list management web application providing full RESTful CRUD operations synced directly to a Google Sheets database.

---

## 🏗️ Architecture & Tech Stack

The application is containerized using **Docker Compose** and consists of two main microservices:

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

- **Frontend / SSR**: Dynamic Jinja2 server-rendered Story view (`api/app/templates/index.html`), social crawler previews (`og_preview.html`), and static assets (`html/assets/`) served directly by Nginx.
- **Backend API**: Python 3.12 Flask REST API (`api/app/app.py`).
- **Database / Storage**: Google Sheets integration via `gspread` (`api/app/sheets.py`).
- **Reverse Proxy**: Nginx (`nginx/nginx.conf`) handling route proxying, `/assets/`, `/favicon.*`, and caching.
- **Security & Crawlers**: Tamper-proof salted anti-forgery URL check codes (`/i/<invitacion_id>_<check_code>`) with crawler bypass for social bots (WhatsApp/Meta) to prevent Google Sheets API exhaustion.
- **Containerization**: Docker Compose (`docker-compose.yml`, `docker-compose.override.yml`).

---

## 📁 Repository Structure

```
├── api/
│   ├── app/
│   │   ├── app.py             # Flask CRUD endpoints (/invitados, /health, /i/<token>)
│   │   ├── sheets.py          # GoogleSheetsTable class (gspread wrapper with thread lock)
│   │   ├── test_sheets.py     # Unit tests for Google Sheets table, crawler bypass & anti-forgery logic
│   │   └── templates/
│   │       ├── index.html     # Main wedding invitation story deck (SSR + Auto-Save + Swipe Gestures)
│   │       ├── og_preview.html# Lightweight Open Graph preview for WhatsApp/social crawlers (0 Sheet calls)
│   │       ├── blank.html     # Blank template for invalid tokens/forgery protection
│   │       └── invitados.html # Admin guest overview table
│   ├── Dockerfile             # Python 3.12 slim container build definition
│   └── requirements.txt       # Flask, gspread, google-auth dependencies
├── html/
│   ├── assets/
│   │   ├── avatar.png         # Circular story header avatar image
│   │   ├── background.jpeg    # High-resolution desktop background
│   │   ├── favicon.svg        # Golden heart vector favicon
│   │   ├── intro.mp4          # Introductory video asset
│   │   └── whatsapp.thumb.jpg # 600x600 WhatsApp thumbnail asset
│   ├── form.html              # Standalone guest registration form

│   ├── favicon.svg            # Fallback favicon asset
│   └── index.html             # Static fallback invitation page
├── nginx/
│   └── nginx.conf             # Nginx server configuration, /assets/, /favicon.*, and API proxy rules
├── secrets/
│   └── credentials.json       # Google Service Account JSON credentials (mounted read-only)
├── docker-compose.yml         # Main Docker Compose configuration
├── docker-compose.override.yml# Local override configuration (ports, mounts)
├── deploy.sh                  # Automated deployment script with remote container refresh
├── project.env                # Project environment variables
├── API.md                     # Full RESTful CRUD API documentation & field specs
└── AGENTS.md                  # Instructions for AI agents (this file)
```

---

## 🔌 API Endpoints Summary

For complete schema details, request/response payloads, and examples, see [`API.md`](file:///home/alfredo/dev/boda/API.md).

All API routes are proxied through Nginx:

| Method | Endpoint | Description | Auth / Parameters |
|---|---|---|---|
| `GET` | `/health` | API Healthcheck | Public |
| `GET` | `/i/<token>` | Tamper-proof invitation story deck (SSR) or crawler OG preview | Public (`<invitacion_id>_<check_code>`) |
| `GET` | `/invitados` | Fetch all guest records | **API Key Required** (`X-API-Key` or `?api_key=`) |
| `GET` | `/invitados/<id>` | Fetch guest by ID | Public (`record_id` path param) |
| `POST` | `/invitados` | Create guest record(s) | **API Key Required** (Single JSON object or array) |
| `POST` | `/invitados/ensure-ids`| Generate missing guest IDs in Sheets | **API Key Required** |
| `PUT / PATCH` | `/invitados/<id>` | Update guest record (RSVP auto-save) | Public (JSON updates) |
| `DELETE` | `/invitados/<id>` | Delete guest record | **API Key Required** |

---

## ⚙️ Environment Variables (`project.env`)

- `API_KEY`: Secret authentication key for admin endpoints (`/invitados` CRUD).
- `BASE_URL`: Base domain URL for invitation links (e.g., `https://nos.vamos.acas.ar`).
- `INVITATION_SALT`: Secret salt used to compute deterministic anti-forgery check codes.
- `VIRTUAL_HOST`: Domain name for reverse proxy routing.
- `LETSENCRYPT_HOST`: Domain name for SSL certificate generation.
- `LETSENCRYPT_EMAIL`: Contact email for SSL notifications.
- `GOOGLE_SHEET_ID`: Unique ID of the target Google Sheet document.
- `GOOGLE_CREDENTIALS_PATH`: Path inside container to Google Service Account credentials (`/secrets/credentials.json`).
- `WORKSHEET_NAME`: Worksheet tab name within the Google Sheet (e.g., `Confirmaciones`).

---

## 🧪 Testing & Verification

- **Backend Unit Tests**:
  Unit tests reside in `api/app/test_sheets.py` covering Google Sheets CRUD, field mapping, salted anti-forgery tokens, WhatsApp link generation, crawler bypass, and route rendering.
  To execute tests inside container:
  ```bash
  docker compose exec api python3 -m unittest test_sheets.py
  ```

- **Deployment Verification**:
  ```bash
  ./deploy.sh
  ```

---

## 🤖 Guidelines for AI Agents

1. **API Contracts & Security**: Preserve response structures (`{"ok": true, "data": ...}`). Administrative endpoints (`GET /invitados`, `POST /invitados`, `DELETE`) require the `API_KEY` (via header `X-API-Key`, `Authorization: Bearer`, or query param `?api_key=`).
2. **Concurrency Safety**: Interactions with Google Sheets in `api/app/sheets.py` are synchronized via `threading.RLock()`. Maintain thread safety for any new sheet operations.
3. **Anti-Forgery Link Protection**: Keep invitation tokens formatted as `{invitacion_id}_{check_code}` (6 hex characters generated with `sha256(f"{invitacion_id}:{INVITATION_SALT}")[:6]`).
4. **Crawler Bypass Optimization**: Requests with crawler User-Agents (WhatsApp, Facebook, Twitter, Telegram) on `/i/<token>` must return `og_preview.html` without querying Google Sheets API.
5. **Auto-Save Engine**: Story RSVP confirmation uses debounced auto-saving (configured via `AUTOSAVE_CONFIG`) with state diffing to minimize Google Sheets API consumption. Immediate flushes trigger on `blur`, accordion toggle, slide change, and `pagehide`/`visibilitychange`.
6. **Navigation Features**: Mobile devices support touch swipe horizontal gestures with slide-over focus animations, pull-to-refresh downward gesture (`window.location.reload()`), and smart vertical form scrolling on the RSVP slide. Desktop mode supports direct slide jumping on preview miniatures.
7. **Rate Limiting**: Nginx rate-limits API requests at 20 req/s with a burst of 30 to prevent API exhaustion.

8. **Environment Security**: Never hardcode credentials. Store configuration parameters in `project.env` and sensitive access tokens in `secrets/credentials.json`.
9. **Code Quality & Testing**: Run unit tests (`api/app/test_sheets.py`) after modifying backend logic in `api/app/`.



