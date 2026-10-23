# 💍 Boda - Wedding Guest & Story Invitation Platform

Plataforma integral para gestión de invitados de boda, confirmación de asistencia (RSVP) en tiempo real, historias interactivas estilo Instagram/TikTok, panel de diseño visual y sincronización bidireccional continua con **Google Sheets**.

---

## 🏗️ Arquitectura del Sistema

La solución está dockerizada con microservicios orquestados mediante **Docker Compose**:

```text
                             ┌──────────────────────┐
                             │    Nginx Proxy       │
                             │   (Container: web)   │
                             └──────────┬───────────┘
                                        │
                       ┌────────────────┴────────────────┐
                       ▼                                 ▼
              Archivos Estáticos                 Flask REST API / SSR
              (/usr/share/nginx/html)             (Container: api)
              - CSS / JS / Favicon                        │
              - /assets/backgrounds/                     ├──► Google Sheets API (gspread)
              - /assets/fonts/                           └──► Telegram Bot API (Notificaciones)
              - /comprobantes/
```

- **Frontend SSR**: Renderizado dinámico con Jinja2 (`index.html`), tarjetas OpenGraph ultralivianas para crawlers de redes (`og_preview.html`) y assets estáticos optimizados (`html/assets/`).
- **Backend API**: Flask en Python 3.12 con soporte asíncrono para caché local persistente en JSON y batch updates a Google Sheets.
- **Base de Datos**: Hoja de cálculo de Google Sheets con bloqueo de concurrencia mediante `threading.RLock()`.
- **Notificaciones**: Bot de Telegram para avisos instantáneos de confirmaciones de asistencia y pagos.
- **Proxy Inverso**: Nginx con rate limiting (20 req/s, burst de 30), compresión y reglas de caché estática.

---

## 🗺️ Directorio Completo de Rutas y Endpoints

### 1. Invitación y Visualización de Historias (SSR / Públicas)

Rutas públicas para que los invitados visualicen la tarjeta interactiva de boda.

| Método | Endpoint | Autenticación | Descripción |
|---|---|---|---|
| `GET` | `/` | Pública | Portada base o resolución por query parameters (`?token=`, `?invitacion_id=`). Si no hay token, retorna página en blanco segura. |
| `GET` | `/<token>`<br>`/i/<token>`<br>`/invitacion/<token>` | Pública | **Ruta principal de la invitación.**<br>• Si la petición proviene de un bot/crawler (WhatsApp, Facebook, Twitter, Telegram), entrega `og_preview.html` (0 llamadas a Sheets).<br>• Si es un usuario humano, valida el token (`<invitacion_id>_<check_code>`), marca `abierto="si"` y renderiza la historia (`index.html`). |
| `GET` | `/<token>/slides`<br>`/i/<token>/slides`<br>`/invitacion/<token>/slides` | Pública | Retorna la lista JSON de slides activos y filtrados según el tipo de invitado (`all`, `invitacion`, `reserva`). |
| `GET` | `/<token>/slide/<slide_id>`<br>`/i/<token>/slide/<slide_id>`<br>`/invitacion/<token>/slide/<slide_id>` | Pública | Retorna el HTML del fragmento del slide solicitado para visualización modular o depuración. |

---

### 2. Confirmación de Asistencia (RSVP) e Interacción del Invitado

Endpoints consumidos por el cliente JavaScript de la historia (`story.js`) para auto-guardado en segundo plano:

| Método | Endpoint | Autenticación | Descripción |
|---|---|---|---|
| `PUT` / `PATCH` | `/invitados/<record_id>` | Pública | **Auto-guardado RSVP.** Actualiza confirmación (`si`/`no`), preferencia de menú (`general`, `vegetariano`, `vegano`), condición de celíaco y nombres. Los cambios se guardan localmente como `LOCAL` y se sincronizan en lote con Google Sheets. |
| `POST` | `/<token>/comprobante`<br>`/i/<token>/comprobante`<br>`/invitacion/<token>/comprobante` | Pública | **Subida de comprobante de pago.** Recibe un archivo (`multipart/form-data`, campo `file`), auto-orienta y comprime imágenes a JPEG optimizado (o guarda PDF), asocia el nombre al grupo en Sheets y envía alerta por Telegram. |
| `DELETE` | `/<token>/comprobante`<br>`/i/<token>/comprobante`<br>`/invitacion/<token>/comprobante` | Pública | Elimina el archivo del comprobante subido y limpia el campo en Google Sheets. |
| `POST` | `/<token>/flush`<br>`/i/<token>/flush`<br>`/invitacion/<token>/flush` | Pública | Fuerza la sincronización inmediata a Google Sheets de cualquier cambio pendiente en caché para los invitados de este token. |
| `GET` | `/comprobantes/<filename>` | Pública | Entrega el archivo de comprobante subido (en producción es servido directamente por Nginx desde `/data/comprobantes/`). |

---

### 3. Registro y Búsqueda Alternativa

| Método | Endpoint | Autenticación | Descripción |
|---|---|---|---|
| `GET` | `/form` | Pública | Formulario web independiente (`form.html`) para registro manual de invitados o búsqueda de invitaciones. |

---

### 4. Gestión Administrativa de Invitados (CRUD)

Endpoints administrativos para consultar, crear y modificar registros.

> 🔒 **Seguridad**: Requieren la variable `API_KEY` configurada en `project.env`. Se puede enviar mediante:
> - Header: `X-API-Key: <clave>`
> - Header: `Authorization: Bearer <clave>`
> - Query Parameter: `?api_key=<clave>`

| Método | Endpoint | Autenticación | Descripción |
|---|---|---|---|
| `GET` | `/invitados` | **API Key** | Retorna la lista completa de todos los invitados en formato JSON: `{"ok": true, "count": N, "data": [...]}`. |
| `GET` | `/invitados/<record_id>` | Pública | Obtiene los detalles de un invitado específico por su ID único de 8 caracteres hexadecimales. |
| `POST` | `/invitados` | **API Key** | **Crear invitado(s).** Acepta un objeto JSON individual o un array de objetos para inserción masiva. Genera automáticamente IDs y URLs de invitación con token si no se proveen. |
| `POST` | `/invitados/ensure-ids` | **API Key** | Examina todas las filas en Google Sheets y genera automáticamente IDs, URLs seguras y enlaces directos de WhatsApp para aquellas filas que no los tengan. |
| `POST` | `/invitados/flush` | **API Key** | Fuerza la sincronización inmediata de todas las filas pendientes marcadas como `LOCAL` dirty hacia Google Sheets mediante un único `batch_update`. |
| `DELETE` | `/invitados/<record_id>` | **API Key** | Elimina permanentemente un registro de la hoja de cálculo de Google Sheets y del archivo de caché local. |
| `GET` | `/invitados-view` | **API Key** | Vista HTML interactiva con tabla de invitados, buscador, filtros de confirmación y estadísticas (`invitados.html`). |

---

### 5. Estudio Visual y Editor de Slides (`/panel`)

Herramienta gráfica web para diseñar los slides de la historia, tipografías, fondos de video/foto y stickers en tiempo real.

| Método | Endpoint | Autenticación | Descripción |
|---|---|---|---|
| `GET` | `/panel` | Modo Dev / Libre | Abre el editor visual (`panel.html`) con previsualización en vivo, emulador móvil, timeline y controles tipográficos. |
| `POST` | `/panel/slides` | Panel | Guarda el array completo de slides en `/data/slides.json`. |
| `POST` | `/panel/slides/new` | Panel | Crea un nuevo slide en el sistema con configuración por defecto. |
| `POST` | `/panel/slide/<slide_id>` | Panel | Actualiza la configuración de un slide específico (duración, visibilidad, elementos, orden). |
| `POST` | `/panel/slide/<slide_id>/delete` | Panel | Elimina un slide del archivo `slides.json`. |
| `GET` | `/panel/slides/download` | Panel | Descarga el archivo de respaldo `slides.json`. |
| `POST` | `/panel/slides/upload` | Panel | Sube y reemplaza el archivo `slides.json` desde un archivo local. |
| `POST` | `/panel/styles` | Panel | Guarda la configuración de estilos globales (colores, fuentes, sombras) en `/data/styles.json`. |
| `GET` | `/panel/styles/download` | Panel | Descarga el archivo de respaldo `styles.json`. |
| `POST` | `/panel/styles/upload` | Panel | Sube y reemplaza la configuración `styles.json`. |
| `POST` | `/panel/upload-background` | Panel | Sube imágenes (.jpg, .png, .webp) o videos (.mp4, .mov, .webm) a la carpeta `/data/backgrounds/`. |
| `POST` | `/panel/rename-background` | Panel | Renombra un fondo existente y actualiza automáticamente las referencias en los slides. |
| `POST` | `/panel/delete-background` | Panel | Elimina un archivo multimedia de `/data/backgrounds/`. |
| `POST` | `/panel/upload-font` | Panel | Sube una fuente tipográfica (.ttf, .otf), la guarda en `/data/fonts/` y regenera las reglas `@font-face` en `fonts.css`. |
| `POST` | `/panel/delete-font` | Panel | Elimina una fuente tipográfica personalizada. |

---

### 6. Integración con Telegram Bot

Alertas en vivo a los novios cuando un invitado confirma asistencia o sube su comprobante.

| Método | Endpoint | Autenticación | Descripción |
|---|---|---|---|
| `GET` | `/telegram/auth-link` | **API Key** | Genera un enlace profundo único (`https://t.me/<bot_name>?start=<secret>`) para que un administrador autorice su chat y reciba notificaciones push. |
| `POST` | `/telegram/webhook` | Token Secreto | Webhook oficial que procesa mensajes y comandos entrantes desde la API de Telegram Bot. |

---

### 7. Diagnóstico y Salud

| Método | Endpoint | Autenticación | Descripción |
|---|---|---|---|
| `GET` | `/health` | Pública | Healthcheck de estado del servicio API. Retorna `{"status": "ok"}` con código HTTP 200. |

---

### 8. Rutas Estáticas Servidas por Nginx

Nginx maneja la entrega de medios y estáticos con políticas de caché optimizadas:

| Ruta | Directorio / Origen | Cabeceras de Caché |
|---|---|---|
| `/assets/` | `/usr/share/nginx/html/assets/` | `Cache-Control: no-cache` (desarrollo rápido) |
| `/assets/fonts/` | `/data/fonts/` | `Cache-Control: no-cache`, con autoindex JSON habilitado |
| `/assets/backgrounds/` | `/data/backgrounds/` | `Cache-Control: public, max-age=86400` (1 día) |
| `/assets/avatar.(jpeg\|png)` | `/data/avatar.(jpeg\|png)` | `Cache-Control: no-cache` |
| `/whatsapp.thumb.(jpeg\|jpg)`| `/data/whatsapp.thumb.jpeg` | Miniatura oficial para previsualización en WhatsApp |
| `/comprobantes/` | `/data/comprobantes/` | `Cache-Control: no-cache` |
| `/favicon.(svg\|ico)` | `/assets/favicon.svg` | `Cache-Control: public, max-age=30d` |

---

## 🔒 Mecanismos de Seguridad y Anti-Fraude

1. **Tokens Anti-Manipulación (`check_code`):**
   Las URLs de invitación tienen el formato `/<invitacion_id>_<check_code>`. El `check_code` es un hash determinístico de 6 caracteres hexadecimales:
   ```python
   sha256(f"{invitacion_id}:{INVITATION_SALT}")[:6]
   ```
   Si el código no coincide exactamente con el salt secreto, la petición es rechazada de inmediato sin consultar Google Sheets.

2. **Bypass Inteligente para Redes Sociales (Crawler Optimization):**
   Cuando se comparte un enlace en WhatsApp, Instagram, Telegram o Facebook, los bots escanean la URL para mostrar el título y la miniatura. La API detecta estos User-Agents y responde inmediatamente con [`og_preview.html`](file:///home/alfredo/dev/boda/api/app/templates/og_preview.html) en <10ms, evitando agotar la cuota de la API de Google Sheets.

3. **Caché Persistente y Escrituras en Lote:**
   - Lecturas: se sirven desde `/data/sheet_cache.json` (`TTL_READ = 10 min`).
   - Escrituras: se guardan instantáneamente en memoria/JSON local y se sincronizan a Google Sheets en lote (`batch_update`) en una única llamada HTTPS cada `TTL_WRITE = 2 min` o mediante eventos de blur/cierre de pestaña.

---

## 🛠️ Comandos de Desarrollo y Verificación

### Iniciar el entorno local con Docker
```bash
docker compose up -d --build
```

### Ejecutar la suite de tests unitarios
```bash
docker compose run --rm api python3 -m unittest test_sheets.py
```

### Ver logs en tiempo real del backend
```bash
docker compose logs -f api
```

### Sincronizar datos multimedia con producción
```bash
./sync_data.sh
```
