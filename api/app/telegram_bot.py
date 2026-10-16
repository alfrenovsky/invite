import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("telegram_bot")


def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [TELEGRAM] {msg}", flush=True)


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BOT_NAME = os.environ.get("TELEGRAM_BOT_NAME", "celiayalfredo_bot").strip()
TELEGRAM_AUTH_SECRET = os.environ.get("TELEGRAM_AUTH_SECRET", "boda_cya_admin_telegram_2027").strip()
SUBSCRIBERS_FILE = os.environ.get("TELEGRAM_SUBSCRIBERS_FILE", "/data/telegram_subscribers.json")

# Cooldown for open notifications (default: 60s / 1 min)
OPEN_COOLDOWN_SECONDS = int(os.environ.get("OPEN_COOLDOWN_SECONDS", "60"))

# Threading locks and trackers
_lock = threading.RLock()
_subscribers_cache = None
_last_opened = {}  # {clean_id: timestamp}
_dirty_rsvp_sessions = {}  # {clean_id: {"time": timestamp, "table": table}}
_rsvp_debounce_timers = {}  # {clean_id: Timer}
_polling_thread = None
_is_polling = False
_table_ref = None


def _normalize_slug(slug: str) -> str:
    return str(slug or "").strip().lower().replace(" ", "_").replace("-", "_")


def get_auth_link() -> str:
    """Returns the deep-link for admin authorization in Telegram."""
    return f"https://t.me/{TELEGRAM_BOT_NAME}?start={TELEGRAM_AUTH_SECRET}"


def _get_tz_now_str() -> str:
    """Returns human-readable current time in Argentina timezone."""
    try:
        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
    return now.strftime("%d/%m/%Y %H:%M hs")


def _load_subscribers() -> list:
    global _subscribers_cache
    with _lock:
        if _subscribers_cache is not None:
            return list(_subscribers_cache)

        if os.path.exists(SUBSCRIBERS_FILE):
            try:
                with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "subscribers" in data:
                        _subscribers_cache = data["subscribers"]
                    elif isinstance(data, list):
                        _subscribers_cache = data
                    else:
                        _subscribers_cache = []
            except Exception as e:
                logger.error(f"[TELEGRAM] Error reading subscribers file: {e}")
                _subscribers_cache = []
        else:
            _subscribers_cache = []

        return list(_subscribers_cache)


def _save_subscribers(subs: list):
    global _subscribers_cache
    with _lock:
        _subscribers_cache = list(subs)
        os.makedirs(os.path.dirname(os.path.abspath(SUBSCRIBERS_FILE)), exist_ok=True)
        tmp_file = f"{SUBSCRIBERS_FILE}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump({"subscribers": _subscribers_cache}, f, indent=2, ensure_ascii=False)
            os.replace(tmp_file, SUBSCRIBERS_FILE)
        except Exception as e:
            logger.error(f"[TELEGRAM] Error writing subscribers file: {e}")


def is_subscribed(chat_id: int | str) -> bool:
    subs = _load_subscribers()
    cid = str(chat_id)
    return any(str(s.get("chat_id")) == cid for s in subs)


def add_subscriber(chat_id: int | str, user_info: dict = None) -> bool:
    with _lock:
        subs = _load_subscribers()
        cid = str(chat_id)
        if any(str(s.get("chat_id")) == cid for s in subs):
            return False

        record = {
            "chat_id": int(chat_id) if str(chat_id).lstrip("-").isdigit() else str(chat_id),
            "username": (user_info or {}).get("username", ""),
            "first_name": (user_info or {}).get("first_name", ""),
            "subscribed_at": int(time.time()),
            "subscribed_date": _get_tz_now_str(),
        }
        subs.append(record)
        _save_subscribers(subs)
        logger.info(f"[TELEGRAM] Added new subscriber: {record}")
        return True


def remove_subscriber(chat_id: int | str) -> bool:
    with _lock:
        subs = _load_subscribers()
        cid = str(chat_id)
        new_subs = [s for s in subs if str(s.get("chat_id")) != cid]
        if len(new_subs) != len(subs):
            _save_subscribers(new_subs)
            logger.info(f"[TELEGRAM] Removed subscriber: {chat_id}")
            return True
        return False


def call_telegram_api(method: str, payload: dict = None) -> dict | None:
    if not TELEGRAM_BOT_TOKEN:
        return None

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
        try:
            return json.loads(err_body)
        except Exception:
            pass
        _log(f"[API_ERROR] {method} failed with code {e.code}: {err_body}")
        return None
    except Exception as e:
        _log(f"[CONN_ERROR] {method} connection failed: {e}")
        return None


def send_message(chat_id: int | str, text: str, parse_mode: str = "HTML") -> bool:
    res = call_telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    })
    return bool(res and res.get("ok"))


def broadcast_message(text: str, parse_mode: str = "HTML"):
    """Broadcasts a notification message asynchronously to all authorized subscribers."""
    def _do_broadcast():
        subs = _load_subscribers()
        if not subs:
            logger.debug("[TELEGRAM] No subscribers to broadcast message.")
            return

        for s in subs:
            cid = s.get("chat_id")
            if cid:
                try:
                    send_message(cid, text, parse_mode=parse_mode)
                except Exception as e:
                    logger.warning(f"[TELEGRAM] Error sending message to {cid}: {e}")

    t = threading.Thread(target=_do_broadcast, daemon=True)
    t.start()


# ==============================================================
# 🎯 Notification Event Handlers
# ==============================================================

def notify_invitation_opened(invitacion_id: str, guest_records: list):
    """
    Notifies when a guest opens the invitation.
    Respects a 1-minute cooldown per invitation_id to prevent spam.
    """
    if not TELEGRAM_BOT_TOKEN or not invitacion_id:
        return

    now = time.time()
    clean_id = _normalize_slug(invitacion_id)

    with _lock:
        last = _last_opened.get(clean_id, 0)
        if now - last < OPEN_COOLDOWN_SECONDS:
            return
        _last_opened[clean_id] = now

    names = []
    for g in guest_records or []:
        n = f"{g.get('nombre', '')} {g.get('apellido', '')}".strip()
        if not n:
            n = "Acompañante"
        names.append(n)

    names_str = ", ".join(names) if names else clean_id.replace("_", " ").title()
    group_title = clean_id.replace("_", " ").title()

    msg = (
        f"💌 <b>Invitación abierta</b>\n"
        f"👥 <b>Grupo:</b> {group_title}\n"
        f"👤 <b>Invitados:</b> {names_str}\n"
        f"🕒 <b>Hora:</b> {_get_tz_now_str()}"
    )
    broadcast_message(msg)


def mark_invitation_dirty(invitacion_id: str, table=None):
    """
    Marks an invitation as modified during the active visit.
    Also starts a 5-second debounce timer that automatically flushes
    if the user does not leave the slide immediately.
    """
    if not invitacion_id:
        return

    clean_id = _normalize_slug(invitacion_id)
    tbl = table or _table_ref

    with _lock:
        _dirty_rsvp_sessions[clean_id] = {"time": time.time(), "table": tbl}

        # Cancel prior timer if active
        prev_timer = _rsvp_debounce_timers.get(clean_id)
        if prev_timer:
            try:
                prev_timer.cancel()
            except Exception:
                pass

        def _delayed_flush():
            with _lock:
                if clean_id not in _dirty_rsvp_sessions:
                    return
                session_data = _dirty_rsvp_sessions.get(clean_id, {})
                t_ref = session_data.get("table") or _table_ref

            guest_list = []
            if t_ref:
                try:
                    guest_list = t_ref.get_by_invitacion(clean_id)
                except Exception as ex:
                    _log(f"Error fetching guests for delayed flush: {ex}")

            notify_rsvp_on_flush(clean_id, guest_list)

        timer = threading.Timer(5.0, _delayed_flush)
        timer.daemon = True
        _rsvp_debounce_timers[clean_id] = timer
        timer.start()


def notify_rsvp_on_flush(invitacion_id: str, guest_records: list = None):
    """
    Sent when leaving the form page/slide, or via delayed debounce timer.
    Consolidates the full group RSVP status into a single clean message.
    """
    if not TELEGRAM_BOT_TOKEN or not invitacion_id:
        return

    clean_id = _normalize_slug(invitacion_id)
    with _lock:
        if clean_id not in _dirty_rsvp_sessions:
            return
        session_data = _dirty_rsvp_sessions.pop(clean_id, {})
        timer = _rsvp_debounce_timers.pop(clean_id, None)
        if timer:
            try:
                timer.cancel()
            except Exception:
                pass

    tbl = session_data.get("table") or _table_ref
    if (not guest_records) and tbl:
        try:
            guest_records = tbl.get_by_invitacion(clean_id)
        except Exception:
            guest_records = []

    group_title = clean_id.replace("_", " ").title()
    lines = [
        "📋 <b>Confirmación RSVP actualizada</b>",
        f"💌 <b>Grupo:</b> {group_title}\n",
    ]

    for g in guest_records or []:
        nombre = f"{g.get('nombre', '')} {g.get('apellido', '')}".strip() or "Acompañante"
        conf = str(g.get("confirmacion", "")).strip().lower()
        menu = str(g.get("menu", "general")).strip().title() or "General"
        celiaco = str(g.get("celiaco", "")).strip().lower() in ("si", "sí", "true", "1")

        if conf in ("si", "sí", "confirmado"):
            status_text = "✅ Asiste"
            extras = [f"Menú: {menu}"]
            if celiaco:
                extras.append("🌾 Sin gluten")
            lines.append(f"• <b>{nombre}</b>: {status_text} ({', '.join(extras)})")
        elif conf in ("no", "rechazado"):
            status_text = "❌ No asiste"
            lines.append(f"• <b>{nombre}</b>: {status_text}")
        else:
            status_text = "⏳ Pendiente"
            lines.append(f"• <b>{nombre}</b>: {status_text}")

    lines.append(f"\n🕒 <b>Hora:</b> {_get_tz_now_str()}")
    broadcast_message("\n".join(lines))


def notify_comprobante_uploaded(invitacion_id: str, filename: str, file_url: str, guest_records: list):
    """
    Notifies when a payment receipt is uploaded.
    """
    if not TELEGRAM_BOT_TOKEN:
        return

    clean_id = _normalize_slug(invitacion_id)
    group_title = clean_id.replace("_", " ").title()
    names = [f"{g.get('nombre', '')} {g.get('apellido', '')}".strip() or "Acompañante" for g in (guest_records or [])]
    names_str = ", ".join(names) if names else group_title

    msg = (
        f"💳 <b>Comprobante de pago recibido</b>\n"
        f"💌 <b>Grupo:</b> {group_title}\n"
        f"👤 <b>Invitados:</b> {names_str}\n"
        f"📎 <b>Archivo:</b> {filename}\n"
        f"🔗 <a href=\"{file_url}\">Ver comprobante en el navegador</a>\n"
        f"🕒 <b>Hora:</b> {_get_tz_now_str()}"
    )
    broadcast_message(msg)


# ==============================================================
# 🤖 Telegram Bot Polling & Command Processing
# ==============================================================

def _get_wedding_stats(table) -> str:
    tbl = table or _table_ref
    if not tbl:
        return "No hay conexión disponible con la base de datos."

    try:
        # 1. Flush pending local writes to Google Sheets
        if hasattr(tbl, "flush_local_to_remote"):
            try:
                tbl.flush_local_to_remote()
            except Exception as fe:
                _log(f"Warning flushing local changes before stats: {fe}")

        # 2. Force-refresh cache directly from Google Sheets
        if hasattr(tbl, "get_all"):
            records = tbl.get_all(force_remote=True) or []
        elif hasattr(tbl, "get_all_records"):
            records = tbl.get_all_records() or []
        else:
            records = []

        total_records = len(records)
        confirmados = sum(1 for r in records if str(r.get("confirmacion", "")).strip().lower() in ("si", "sí", "confirmado"))
        rechazados = sum(1 for r in records if str(r.get("confirmacion", "")).strip().lower() in ("no", "rechazado"))
        pendientes = total_records - confirmados - rechazados
        pagados = sum(1 for r in records if str(r.get("pagado", "")).strip())
        tarjetas_pagadas = pagados

        # Group records by tarjeta / invitation
        groups = {}
        for r in records:
            inv_id = str(r.get("invitacion_id") or r.get("invitacion") or r.get("id") or "").strip().lower().replace(" ", "_").replace("-", "_")
            if not inv_id:
                inv_id = f"guest_{r.get('id', '')}"
            groups.setdefault(inv_id, []).append(r)

        total_tarjetas = len(groups)
        tarjetas_completas_si = 0
        tarjetas_completas_no = 0
        tarjetas_pendientes = 0
        tarjetas_parciales = 0
        tarjetas_abiertas = 0

        inv_pagadas_completas = 0
        inv_pagadas_parciales = 0

        for g in groups.values():
            g_total = len(g)
            g_si = sum(1 for r in g if str(r.get("confirmacion", "")).strip().lower() in ("si", "sí", "confirmado"))
            g_no = sum(1 for r in g if str(r.get("confirmacion", "")).strip().lower() in ("no", "rechazado"))
            g_pend = g_total - g_si - g_no

            if any(str(r.get("abierto", "")).strip().lower() in ("si", "sí", "true", "1") for r in g):
                tarjetas_abiertas += 1

            if g_si == g_total:
                tarjetas_completas_si += 1
            elif g_no == g_total:
                tarjetas_completas_no += 1
            elif g_pend == g_total:
                tarjetas_pendientes += 1
            else:
                tarjetas_parciales += 1

            # Payment evaluation for group/invitacion:
            # If anyone confirmed 'si', only confirmed attendees need to pay.
            # Otherwise (pending), consider all non-rejected members.
            attending = [r for r in g if str(r.get("confirmacion", "")).strip().lower() in ("si", "sí", "confirmado")]
            if attending:
                target = attending
            else:
                target = [r for r in g if str(r.get("confirmacion", "")).strip().lower() not in ("no", "rechazado")]

            if target:
                paid_in_group = sum(1 for r in target if str(r.get("pagado", "")).strip())
                if paid_in_group == len(target) and paid_in_group > 0:
                    inv_pagadas_completas += 1
                elif paid_in_group > 0:
                    inv_pagadas_parciales += 1

        # Group records by first word in dequien (origin)
        dequien_groups = {}
        for r in records:
            dq = str(r.get("dequien", "") or "").strip()
            first_word = dq.split()[0].capitalize() if dq else "Sin definir"
            dequien_groups.setdefault(first_word, []).append(r)

        dequien_lines = []
        for fw, g_recs in sorted(dequien_groups.items(), key=lambda x: -len(x[1])):
            g_tot = len(g_recs)
            g_si = sum(1 for r in g_recs if str(r.get("confirmacion", "")).strip().lower() in ("si", "sí", "confirmado"))
            g_no = sum(1 for r in g_recs if str(r.get("confirmacion", "")).strip().lower() in ("no", "rechazado"))
            g_pend = g_tot - g_si - g_no
            g_pag = sum(1 for r in g_recs if str(r.get("pagado", "")).strip().lower() in ("si", "sí", "pagado", "1", "true"))
            dequien_lines.append(f"• <b>{fw}:</b> <b>{g_tot}</b> (✅ {g_si} · ❌ {g_no} · ⏳ {g_pend} · 💳 {g_pag})")

        dequien_section = ""
        if dequien_lines:
            dequien_section = "👥 <b>Por origen (de quién):</b>\n" + "\n".join(dequien_lines) + "\n\n"

        return (
            f"📊 <b>Estado de la boda (en vivo):</b>\n\n"
            f"👤 <b>Invitados individuales:</b>\n"
            f"• Total: <b>{total_records}</b>\n"
            f"• ✅ Confirmados: <b>{confirmados}</b>\n"
            f"• ❌ No asisten: <b>{rechazados}</b>\n"
            f"• ⏳ Pendientes: <b>{pendientes}</b>\n\n"
            f"{dequien_section}"
            f"💌 <b>Tarjetas / Grupos:</b>\n"
            f"• Total tarjetas: <b>{total_tarjetas}</b>\n"
            f"• 📖 Abiertas: <b>{tarjetas_abiertas}</b>\n"
            f"• ✅ Completas confirmadas: <b>{tarjetas_completas_si}</b>\n"
            f"• ❌ Tarjetas completas que no asisten: <b>{tarjetas_completas_no}</b>\n"
            f"• ⚠️ Tarjetas con confirmación parcial: <b>{tarjetas_parciales}</b>\n"
            f"• ⏳ Sin responder: <b>{tarjetas_pendientes}</b>\n\n"
            f"💳 <b>Pagos:</b>\n"
            f"• ✅ Grupos completos pagados: <b>{inv_pagadas_completas}</b>\n"
            f"• ⚠️ Invitaciones con pago parcial: <b>{inv_pagadas_parciales}</b>\n"
            f"• 🎫 Invitaciones individuales pagadas: <b>{tarjetas_pagadas}</b>\n\n"
            f"🔄 <i>Sincronizado con Google Sheets:</i> {_get_tz_now_str()}"
        )
    except Exception as e:
        _log(f"Error fetching wedding stats: {e}")
        return "Error al obtener estadísticas de Google Sheets."


def _handle_update(update: dict, table=None):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    from_user = msg.get("from") or {}
    user_name = from_user.get("first_name") or from_user.get("username") or "Admin"

    if not chat_id or not text:
        return

    # 1. /start command
    if text.startswith("/start"):
        parts = text.split()
        provided_token = parts[1].strip() if len(parts) > 1 else ""

        if provided_token == TELEGRAM_AUTH_SECRET:
            added = add_subscriber(chat_id, from_user)
            reply = (
                f"🎉 <b>¡Autorizado con éxito!</b>\n\n"
                f"Hola <b>{user_name}</b>, quedaste registrado en <b>@{TELEGRAM_BOT_NAME}</b>.\n\n"
                f"A partir de ahora vas a recibir acá las notificaciones de la boda:\n"
                f"• 💌 Apertura de invitaciones\n"
                f"• 📋 Confirmaciones RSVP (al salir del formulario)\n"
                f"• 💳 Comprobantes de transferencia\n\n"
                f"Comandos útiles:\n"
                f"• /status - Ver resumen en tiempo real\n"
                f"• /stop - Desactivar notificaciones"
            )
            send_message(chat_id, reply)
        else:
            if is_subscribed(chat_id):
                send_message(
                    chat_id,
                    f"👋 Hola <b>{user_name}</b>, ya estás registrado para recibir notificaciones.\n\n"
                    f"• /status - Ver estado de invitados\n"
                    f"• /stop - Desactivar notificaciones"
                )
            else:
                send_message(
                    chat_id,
                    "🔒 <b>Bot privado</b>\n\n"
                    "Este bot es exclusivo para los novios. Para autorizarte, "
                    "utilizá el enlace especial de autorización proporcionado por el sistema."
                )
        return

    # 2. Check if subscribed for remaining commands
    if not is_subscribed(chat_id):
        send_message(
            chat_id,
            "🔒 <b>Acceso no autorizado</b>\n\n"
            "Por favor ingresá usando el enlace de autorización."
        )
        return

    # 3. /status command
    if text == "/status":
        stats_text = _get_wedding_stats(table or _table_ref)
        send_message(chat_id, stats_text)
        return

    # 4. /stop command
    if text == "/stop":
        remove_subscriber(chat_id)
        send_message(chat_id, "🔕 <b>Notificaciones desactivadas.</b> Para volver a activarlas, usá tu enlace de autorización.")
        return

    # 5. Fallback help for authorized user
    send_message(
        chat_id,
        f"👋 Hola <b>{user_name}</b>.\n\n"
        f"Comandos disponibles:\n"
        f"• /status - Ver resumen de confirmaciones y pagos\n"
        f"• /stop - Desactivar notificaciones"
    )


def handle_webhook_update(update: dict, table=None) -> bool:
    """Processes an incoming Telegram webhook update."""
    if not isinstance(update, dict):
        return False
    try:
        _handle_update(update, table or _table_ref)
        return True
    except Exception as e:
        _log(f"Error handling webhook update: {e}")
        return False


def _polling_loop(table=None):
    global _is_polling
    offset = 0
    _log(f"Starting bot worker for @{TELEGRAM_BOT_NAME}...")

    while _is_polling:
        try:
            # Check if webhook is active
            wh_info = call_telegram_api("getWebhookInfo")
            if wh_info and wh_info.get("ok") and wh_info.get("result", {}).get("url"):
                # Webhook is active, standby without calling getUpdates to prevent 409 Conflict
                time.sleep(30)
                continue

            res = call_telegram_api("getUpdates", {"offset": offset, "timeout": 25, "limit": 50})
            if res and res.get("ok"):
                updates = res.get("result", [])
                for u in updates:
                    offset = max(offset, u["update_id"] + 1)
                    try:
                        _handle_update(u, table)
                    except Exception as ex:
                        _log(f"Error handling update: {ex}")
            elif res and res.get("error_code") == 409:
                time.sleep(30)
            else:
                time.sleep(2)
        except Exception as e:
            _log(f"Polling iteration error: {e}")
            time.sleep(5)


def start_telegram_bot(table=None):
    """Starts the Telegram bot polling thread if configured."""
    global _polling_thread, _is_polling, _table_ref
    if not TELEGRAM_BOT_TOKEN:
        _log("TELEGRAM_BOT_TOKEN not configured. Skipping bot start.")
        return

    if _polling_thread and _polling_thread.is_alive():
        return

    _table_ref = table
    _is_polling = True
    _polling_thread = threading.Thread(target=_polling_loop, args=(table,), daemon=True)
    _polling_thread.start()
    _log(f"Bot thread started successfully. Auth link: {get_auth_link()}")


def stop_telegram_bot():
    """Stops the Telegram bot polling thread."""
    global _is_polling
    _is_polling = False
