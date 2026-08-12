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

- **Frontend**: Static HTML/JS pages (`html/index.html`, `html/form.html`) served directly by Nginx.
- **Backend API**: Python 3.12 Flask REST API (`api/app/app.py`).
- **Database / Storage**: Google Sheets integration via `gspread` (`api/app/sheets.py`).
- **Reverse Proxy**: Nginx (`nginx/nginx.conf`) handling route proxying and static asset serving.
- **Containerization**: Docker Compose (`docker-compose.yml`, `docker-compose.override.yml`).

---

## 📁 Repository Structure

```
├── api/
│   ├── app/
│   │   ├── app.py             # Flask CRUD endpoints (/invitados, /health)
│   │   ├── sheets.py          # GoogleSheetsTable class (gspread wrapper with thread lock)
│   │   └── test_sheets.py     # Unit tests for Google Sheets table logic
│   ├── Dockerfile             # Python 3.12 slim container build definition
│   └── requirements.txt       # Flask, gspread, google-auth dependencies
├── html/
│   ├── index.html             # Main wedding invitation page
│   ├── form.html              # Guest registration form
│   └── whatsapp.thumb.jpg     # Thumbnail asset
├── nginx/
│   └── nginx.conf             # Nginx server configuration and API proxy rules
├── secrets/
│   └── credentials.json       # Google Service Account JSON credentials (mounted read-only)
├── docker-compose.yml         # Main Docker Compose configuration
├── docker-compose.override.yml# Local override configuration (ports, mounts)
├── project.env                # Project environment variables
├── API.md                     # Full RESTful CRUD API documentation
└── AGENTS.md                  # Instructions for AI agents (this file)
```

---

## 🔌 API Endpoints Summary

For complete schema details, request/response payloads, and examples, see [`API.md`](file:///home/alfredo/dev/boda/API.md).

All API routes are proxied through Nginx:

| Method | Endpoint | Description | Payload / Parameters |
|---|---|---|---|
| `GET` | `/health` | API Healthcheck | None |
| `GET` | `/invitados` | Fetch all guest records | None |
| `GET` | `/invitados/<id>` | Fetch guest by ID | `record_id` (path param) |
| `POST` | `/invitados` | Create guest record(s) | Single JSON object or array of objects |
| `PUT / PATCH` | `/invitados/<id>` | Update guest record | JSON updates |
| `DELETE` | `/invitados/<id>` | Delete guest record | None |

---

## ⚙️ Environment Variables (`project.env`)

- `VIRTUAL_HOST`: Domain name for reverse proxy routing.
- `LETSENCRYPT_HOST`: Domain name for SSL certificate generation.
- `LETSENCRYPT_EMAIL`: Contact email for SSL notifications.
- `GOOGLE_SHEET_ID`: Unique ID of the target Google Sheet document.
- `GOOGLE_CREDENTIALS_PATH`: Path inside container to Google Service Account credentials (`/secrets/credentials.json`).
- `WORKSHEET_NAME`: Worksheet tab name within the Google Sheet (e.g., `Respuestas`).

---

## 🧪 Testing & Verification

- **Backend Unit Tests**:
  Unit tests for Google Sheets integration logic reside in `api/app/test_sheets.py`.
  To execute tests inside container:
  ```bash
  docker compose exec api python3 -m unittest test_sheets.py
  ```

- **Docker Compose Command Verification**:
  Validate container setup and configuration:
  ```bash
  docker compose config
  docker compose up --build -d
  ```

---

## 🤖 Guidelines for AI Agents

1. **API Contracts**: Preserve response structures (`{"ok": true, "data": ...}`).
2. **Concurrency Safety**: Interactions with Google Sheets in `api/app/sheets.py` are synchronized via `threading.Lock()`. Maintain thread safety for any new sheet operations.
3. **Environment Security**: Never hardcode credentials. Store configuration parameters in `project.env` and sensitive access tokens in `secrets/credentials.json`.
4. **Code Quality & Testing**: Run unit tests (`api/app/test_sheets.py`) after modifying backend logic in `api/app/`.
