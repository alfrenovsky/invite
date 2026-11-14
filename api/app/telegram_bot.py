import json
import logging
import html
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from sheets import (
    generate_whatsapp_url,
    generate_invitation_url,
    get_group_target_type,
    clean_phone_number,
    BASE_URL,
)

logger = logging.getLogger("telegram_bot")


TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BOT_NAME = os.environ.get("TELEGRAM_BOT_NAME", "celiayalfredo_bot").strip()
TELEGRAM_AUTH_SECRET = os.environ.get("TELEGRAM_AUTH_SECRET", "boda_cya_admin_telegram_2027").strip()
SUBSCRIBERS_FILE = os.environ.get("TELEGRAM_SUBSCRIBERS_FILE", "/data/telegram_subscribers.json")
TELEGRAM_LOG_FILE = os.environ.get("TELEGRAM_LOG_FILE", "/data/telegram.log")
TELEGRAM_COMPROBANTES_DIR = os.environ.get("COMPROBANTES_DIR") or "/data/comprobantes"


def _log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [TELEGRAM] {msg}"
    print(line, flush=True)
    log_path = TELEGRAM_LOG_FILE
    if not log_path:
        return
    try:
        with _lock:
            log_dir = os.path.dirname(os.path.abspath(log_path))
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass

# Cooldown for open notifications (default: 60s / 1 min)
OPEN_COOLDOWN_SECONDS = int(os.environ.get("OPEN_COOLDOWN_SECONDS", "60"))

# Threading locks and trackers
_lock = threading.RLock()
_subscribers_cache = None
_last_opened = {}  # {clean_id: timestamp}
_dirty_rsvp_sessions = {}  # {clean_id: {"time": timestamp, "table": table}}
_rsvp_debounce_timers = {}  # {clean_id: Timer}
_user_states = {}  # {str(chat_id): state_str}
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
        _log(f"[SUBSCRIBER:ADD] chat_id={chat_id} user={record.get('username') or record.get('first_name') or '-'}")
        logger.info(f"[TELEGRAM] Added new subscriber: {record}")
        return True


def remove_subscriber(chat_id: int | str) -> bool:
    with _lock:
        subs = _load_subscribers()
        cid = str(chat_id)
        new_subs = [s for s in subs if str(s.get("chat_id")) != cid]
        if len(new_subs) != len(subs):
            _save_subscribers(new_subs)
            _log(f"[SUBSCRIBER:REMOVE] chat_id={chat_id}")
            logger.info(f"[TELEGRAM] Removed subscriber: {chat_id}")
            return True
        return False


def call_telegram_api(method: str, payload: dict = None) -> dict | None:
    if os.environ.get("TESTING") == "1" or os.environ.get("FLASK_ENV") == "testing":
        return {"ok": True, "result": {}}

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


def send_message(chat_id: int | str, text: str, parse_mode: str = "HTML", reply_markup: dict = None) -> bool:
    if os.environ.get("TESTING") == "1" or os.environ.get("FLASK_ENV") == "testing":
        return True

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    res = call_telegram_api("sendMessage", payload)
    return bool(res and res.get("ok"))


def _send_telegram_multipart(method: str, chat_id: int | str, field_name: str, file_bytes: bytes, filename: str, caption: str = "", reply_markup: dict = None) -> bool:
    if os.environ.get("TESTING") == "1" or os.environ.get("FLASK_ENV") == "testing":
        return True
    if not TELEGRAM_BOT_TOKEN:
        return False

    boundary = f"----BodaTgBoundary{uuid.uuid4().hex}"
    parts = []

    def _add_field(name: str, value: str):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))

    _add_field("chat_id", str(chat_id))
    if caption:
        _add_field("caption", caption)
        _add_field("parse_mode", "HTML")
    if reply_markup:
        _add_field("reply_markup", json.dumps(reply_markup))

    safe_name = os.path.basename(filename or "comprobante.bin")
    parts.append(
        (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"{field_name}\"; filename=\"{safe_name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return bool(res and res.get("ok"))
    except Exception as e:
        _log(f"[MEDIA_ERROR] {method} failed: {e}")
        return False


def send_photo_bytes(chat_id: int | str, image_bytes: bytes, filename: str = "comprobante.png", caption: str = "", reply_markup: dict = None) -> bool:
    return _send_telegram_multipart("sendPhoto", chat_id, "photo", image_bytes, filename, caption=caption, reply_markup=reply_markup)


def send_document_bytes(chat_id: int | str, file_bytes: bytes, filename: str = "comprobante.pdf", caption: str = "", reply_markup: dict = None) -> bool:
    return _send_telegram_multipart("sendDocument", chat_id, "document", file_bytes, filename, caption=caption, reply_markup=reply_markup)


def broadcast_message(text: str, parse_mode: str = "HTML", reply_markup: dict = None):
    """Broadcasts a notification message asynchronously to all authorized subscribers."""
    if os.environ.get("TESTING") == "1" or os.environ.get("FLASK_ENV") == "testing":
        return

    def _do_broadcast():
        subs = _load_subscribers()
        if not subs:
            logger.debug("[TELEGRAM] No subscribers to broadcast message.")
            return

        for s in subs:
            cid = s.get("chat_id")
            if cid:
                try:
                    send_message(cid, text, parse_mode=parse_mode, reply_markup=reply_markup)
                except Exception as e:
                    logger.warning(f"[TELEGRAM] Error sending message to {cid}: {e}")

    t = threading.Thread(target=_do_broadcast, daemon=True)
    t.start()


# ==============================================================
# 🎯 Notification Event Handlers
# ==============================================================

def notify_invitation_opened(invitacion_id: str, guest_records: list, open_count: int = None):
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
    count_suffix = f" (Visita #{open_count})" if (isinstance(open_count, (int, float)) and open_count > 1) else ""

    msg = (
        f"💌 <b>Invitación abierta{count_suffix}</b>\n"
        f"👥 <b>Grupo:</b> {group_title}\n"
        f"👤 <b>Invitados:</b> {names_str}\n"
        f"🕒 <b>Hora:</b> {_get_tz_now_str()}"
    )
    _log(f"[NOTIFY:OPEN] invitacion={clean_id} count={open_count}")
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
        menu = str(g.get("pa") or g.get("ap") or g.get("menu") or "").strip().title()
        celiaco = str(g.get("celiaco", "")).strip().lower() in ("si", "sí", "true", "1")

        if conf in ("si", "sí", "confirmado"):
            status_text = "✅ Asiste"
            extras = []
            if menu:
                extras.append(f"Menú: {menu}")
            if celiaco:
                extras.append("🌾 Sin gluten")
            extra_str = f" ({', '.join(extras)})" if extras else ""
            lines.append(f"• <b>{nombre}</b>: {status_text}{extra_str}")
        elif conf in ("no", "rechazado"):
            status_text = "❌ No asiste"
            lines.append(f"• <b>{nombre}</b>: {status_text}")
        else:
            status_text = "⏳ Pendiente"
            lines.append(f"• <b>{nombre}</b>: {status_text}")

    lines.append(f"\n🕒 <b>Hora:</b> {_get_tz_now_str()}")
    _log(f"[NOTIFY:RSVP] invitacion={clean_id} guests={len(guest_records or [])}")
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
    _log(f"[NOTIFY:COMPROBANTE] invitacion={clean_id} file={filename}")
    markup = {
        "inline_keyboard": [
            [
                {"text": "👁️ Ver comprobante", "callback_data": f"pagos_ver:{clean_id[:46]}:-1"},
                {"text": "💵 Confirmar monto", "callback_data": f"pagos_comp_m:{clean_id[:46]}:-1"},
            ]
        ]
    }
    broadcast_message(msg, reply_markup=markup)


# ==============================================================
# 🤖 Telegram Bot Polling & Command Processing
# ==============================================================

def _is_record_opened(rec) -> bool:
    val = str(rec.get("abierto", "") or "").strip().lower()
    return val in ("si", "sí", "true") or (val.isdigit() and int(val) > 0)


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
        pagados = sum(1 for r in records if _get_paid_amount(r) > 0)
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

            if any(_is_record_opened(r) for r in g):
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
                paid_in_group = sum(1 for r in target if _is_paid_record(r))
                partial_in_group = any(_get_paid_amount(r) > 0 for r in target)
                if paid_in_group == len(target) and paid_in_group > 0:
                    inv_pagadas_completas += 1
                elif paid_in_group > 0 or partial_in_group:
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
            g_pag = sum(1 for r in g_recs if _get_paid_amount(r) > 0)
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


def _send_chunked_messages(chat_id: int | str, header: str, items: list[str], max_len: int = 3500, reply_markup: dict = None):
    """
    Sends a list of formatted item strings in chunks that fit within Telegram's 4096-character limit.
    """
    if not items:
        if header:
            send_message(chat_id, header, reply_markup=reply_markup)
        return

    current_chunk = [header] if header else []
    current_len = len(header) + 2 if header else 0

    for item in items:
        item_len = len(item) + 2
        if current_len + item_len > max_len and current_chunk:
            send_message(chat_id, "\n\n".join(current_chunk))
            current_chunk = [item]
            current_len = item_len
        else:
            current_chunk.append(item)
            current_len += item_len

    if current_chunk:
        send_message(chat_id, "\n\n".join(current_chunk), reply_markup=reply_markup)


def _normalize_search_text(text: str) -> str:
    return unicodedata.normalize("NFKD", str(text or "")).encode("ASCII", "ignore").decode("ASCII").lower().strip()


def _get_main_menu_markup() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Estado / Resumen", "callback_data": "cmd:status"},
                {"text": "📤 Enviar Invitaciones", "callback_data": "cmd:send"},
            ],
            [
                {"text": "🔍 Buscar y Reenviar", "callback_data": "send:search"},
                {"text": "📵 Sin Teléfono", "callback_data": "cmd:sintelefono"},
            ],
            [
                {"text": "💳 Pagos", "callback_data": "cmd:pagos"},
            ],
        ]
    }


def _send_main_menu(chat_id: int | str, user_name: str = "Admin"):
    send_message(
        chat_id,
        f"👋 Hola <b>{html.escape(user_name or 'Admin')}</b>. ¿Qué querés hacer?",
        reply_markup=_get_main_menu_markup(),
    )


def _prompt_search_query(chat_id: int | str):
    with _lock:
        _user_states[str(chat_id)] = "awaiting_search_query"
    send_message(
        chat_id,
        "🔍 <b>Buscar invitado</b>\n\nEscribí un nombre, apellido, tarjeta o palabra para buscar:",
        reply_markup={"inline_keyboard": [[{"text": "❌ Cancelar", "callback_data": "cmd:cancel"}]]},
    )


def _format_guest_icons(r: dict, opened_invitations: set) -> str:
    icons = []
    conf = str(r.get("confirmacion", "") or "").strip().lower()
    if conf in ("si", "sí", "confirmado"):
        icons.append("🟢")
    elif conf in ("no", "rechazado"):
        icons.append("🔴")
    else:
        icons.append("⏳")

    nivel = str(r.get("nivel", "") or "").strip().lower()
    if nivel == "reserva":
        icons.append("🎫")
    elif nivel in ("invitado", "invitados", "invitacion"):
        icons.append("💌")

    if _get_paid_amount(r) > 0:
        icons.append("💳")

    menu_val = str(r.get("pa") or r.get("ap") or r.get("menu") or "").strip().lower()
    if not menu_val:
        if str(r.get("pa_vegano", "") or "").strip().lower() in ("si", "sí", "true", "1"):
            menu_val = "vegano"
        elif str(r.get("pa_vegetariano", "") or "").strip().lower() in ("si", "sí", "true", "1"):
            menu_val = "vegetariano"
        elif str(r.get("pa_general", "") or "").strip().lower() in ("si", "sí", "true", "1"):
            menu_val = "general"

    if menu_val == "vegano":
        icons.append("🌱")
    elif menu_val == "vegetariano":
        icons.append("🥬")
    elif menu_val == "general":
        icons.append("🍽️")

    celiaco = str(r.get("celiaco") or r.get("pa_celiaco") or "").strip().lower() in ("si", "sí", "true", "1")
    if celiaco:
        icons.append("🌾")

    inv_slug = str(r.get("invitacion_id") or r.get("invitacion") or r.get("id") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if _is_record_opened(r) or (inv_slug and inv_slug in opened_invitations):
        icons.append("👀")

    return " ".join(icons)


def _handle_search_flow(chat_id: int | str, query: str, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    norm_query = _normalize_search_text(query)
    if not norm_query:
        _prompt_search_query(chat_id)
        return

    records = tbl.get_all() or []
    opened_invitations = {
        str(rec.get("invitacion_id") or rec.get("invitacion") or rec.get("id") or "").strip().lower().replace(" ", "_").replace("-", "_")
        for rec in records
        if _is_record_opened(rec)
    }

    matches = []
    for r in records:
        full_name = f"{r.get('nombre', '')} {r.get('apellido', '')}".strip()
        inv_id = str(r.get("invitacion_id") or r.get("invitacion") or "").strip()
        searchable = " ".join([
            _normalize_search_text(full_name),
            _normalize_search_text(inv_id),
        ])
        if norm_query in searchable:
            matches.append(r)

    nav_markup = {
        "inline_keyboard": [
            [
                {"text": "🔍 Buscar otro", "callback_data": "send:search"},
                {"text": "🏠 Menú", "callback_data": "cmd:menu"},
            ]
        ]
    }

    _log(f"[SEARCH] chat_id={chat_id} query={query!r} matches={len(matches)}")

    if not matches:
        send_message(
            chat_id,
            f'🔍 No se encontraron invitados para "<b>{html.escape(query)}</b>".',
            reply_markup=nav_markup,
        )
        return

    matches.sort(key=lambda r: _normalize_search_text(f"{r.get('nombre', '')} {r.get('apellido', '')}") or "zzz")

    base_site = (BASE_URL or "https://nos.vamos.acas.ar").rstrip("/")
    header = f'🔍 <b>Resultados para "{html.escape(query)}" ({len(matches)}):</b>'
    items = []
    for r in matches:
        nombre = f"{r.get('nombre', '')} {r.get('apellido', '')}".strip() or "Invitado"
        clean_p = clean_phone_number(r.get("telefono", ""))
        gid = str(r.get("id", "") or "").strip()
        icons_str = _format_guest_icons(r, opened_invitations)
        if clean_p:
            send_url = f"{base_site}/send/{clean_p}?id={gid}" if gid else f"{base_site}/send/{clean_p}"
            name_part = f'👉 <a href="{send_url}"><b>{html.escape(nombre)}</b></a>'
        else:
            name_part = f"👤 <b>{html.escape(nombre)}</b>"
        items.append(f"{name_part} {icons_str}".strip())

    _send_chunked_messages(chat_id, header, items, reply_markup=nav_markup)


def _handle_send_command(chat_id: int | str, arg: str, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    records = tbl.get_all()
    if not records:
        send_message(chat_id, "No se encontraron invitados.")
        return

    # Extract all distinct dequien values and count non-sent pending contacts
    pending_by_dequien = {}
    for r in records:
        dq = str(r.get("dequien", "") or "").strip()
        if not dq:
            continue
        if dq not in pending_by_dequien:
            pending_by_dequien[dq] = 0
        phone = clean_phone_number(r.get("telefono", ""))
        enviado = str(r.get("enviado", "") or "").strip().lower()
        if phone and enviado not in ("si", "sí", "true", "1"):
            pending_by_dequien[dq] += 1

    sorted_dequien = sorted(pending_by_dequien.keys())

    if not sorted_dequien:
        send_message(chat_id, "ℹ️ No hay registros con 'de quien' configurado en la base de datos.")
        return

    arg = (arg or "").strip()

    if arg.lower() in ("search", "buscar"):
        _prompt_search_query(chat_id)
        return

    if not arg:
        # Build interactive inline buttons (2 per row)
        inline_keyboard = []
        row = []
        for dq in sorted_dequien:
            count = pending_by_dequien[dq]
            btn_text = f"{dq} ({count})" if count > 0 else f"{dq} (0)"
            row.append({"text": btn_text, "callback_data": f"send:{dq}"})
            if len(row) == 2:
                inline_keyboard.append(row)
                row = []
        if row:
            inline_keyboard.append(row)

        inline_keyboard.append([{"text": "🔍 Buscar para Reenviar", "callback_data": "send:search"}])

        cmd_list = "\n".join(f"• <code>/send {dq}</code> ({pending_by_dequien[dq]} pendientes)" for dq in sorted_dequien)
        send_message(
            chat_id,
            f"📤 <b>¿De quién querés enviar invitaciones?</b>\n\n"
            f"Elegí una opción para ver los contactos pendientes:\n\n"
            f"{cmd_list}",
            reply_markup={"inline_keyboard": inline_keyboard} if inline_keyboard else None
        )
        return

    target = arg.lower()
    matching_guests = []
    for r in records:
        dq = str(r.get("dequien", "") or "").strip().lower()
        first_word = dq.split()[0] if dq else ""
        if dq == target or first_word == target or dq.startswith(target):
            phone = clean_phone_number(r.get("telefono", ""))
            enviado = str(r.get("enviado", "") or "").strip().lower()
            if phone and enviado not in ("si", "sí", "true", "1"):
                matching_guests.append(r)

    display_orig = arg
    for orig in sorted_dequien:
        if orig.lower() == target or orig.lower().split()[0] == target:
            display_orig = orig
            break

    if not matching_guests:
        send_message(
            chat_id,
            f"✅ No hay invitaciones pendientes con teléfono para <b>{html.escape(display_orig)}</b>\n"
            f"(todas fueron enviadas o no tienen teléfono registrado)."
        )
        return

    # Sort matching guests alphabetically by display name (accent-insensitive)
    def _sort_name_key(r):
        full = f"{r.get('nombre', '')} {r.get('apellido', '')}".strip()
        norm = unicodedata.normalize("NFKD", full).encode("ASCII", "ignore").decode("ASCII").lower()
        return norm or "zzz"

    matching_guests.sort(key=_sort_name_key)

    opened_invitations = {
        str(rec.get("invitacion_id") or rec.get("invitacion") or rec.get("id") or "").strip().lower().replace(" ", "_").replace("-", "_")
        for rec in records
        if _is_record_opened(rec)
    }

    # Map invitation slug to all guests to identify companions
    inv_all_guests = {}
    for rec in records:
        slug = str(rec.get("invitacion_id") or rec.get("invitacion") or rec.get("id") or "").strip().lower().replace(" ", "_").replace("-", "_")
        if slug:
            inv_all_guests.setdefault(slug, []).append(rec)

    header = f"📤 <b>Invitaciones para enviar ({html.escape(display_orig)}) - {len(matching_guests)} pendientes:</b>"
    items = []
    has_any_opened = False
    base_site = (BASE_URL or "https://nos.vamos.acas.ar").rstrip("/")
    for r in matching_guests:
        nombre = f"{r.get('nombre', '')} {r.get('apellido', '')}".strip() or "Invitado"
        clean_p = clean_phone_number(r.get("telefono", ""))
        gid = str(r.get("id", "") or "").strip()
        send_url = f"{base_site}/send/{clean_p}?id={gid}" if gid else f"{base_site}/send/{clean_p}"
        tel_display = str(r.get("telefono", "") or "").strip()
        tel_suffix = f" ({html.escape(tel_display)})" if tel_display else ""

        inv_slug = str(r.get("invitacion_id") or r.get("invitacion") or r.get("id") or "").strip().lower().replace(" ", "_").replace("-", "_")

        # Other guests belonging to the same invitation card
        other_guests = [
            o for o in inv_all_guests.get(inv_slug, [])
            if str(o.get("id", "")).strip() != str(r.get("id", "")).strip()
        ]
        if other_guests:
            other_names = [
                html.escape(f"{o.get('nombre', '')} {o.get('apellido', '')}".strip() or "Acompañante")
                for o in other_guests
            ]
            others_suffix = f" — {', '.join(other_names)}"
        else:
            others_suffix = ""

        is_opened = (
            _is_record_opened(r)
            or (inv_slug and inv_slug in opened_invitations)
        )
        if is_opened:
            has_any_opened = True
            opened_indicator = " 👀"
        else:
            opened_indicator = ""

        items.append(f'👉 <a href="{send_url}"><b>{html.escape(nombre)}</b></a>{tel_suffix}{others_suffix}{opened_indicator}')

    if has_any_opened:
        items.append("<i>(👀 = tarjeta ya abierta)</i>")

    _send_chunked_messages(chat_id, header, items)


def _handle_sintelefono_command(chat_id: int | str, arg: str, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    records = tbl.get_all()
    if not records:
        send_message(chat_id, "No se encontraron invitados.")
        return

    # Group records by tarjeta / invitation
    groups = {}
    for r in records:
        inv_id = str(r.get("invitacion_id") or r.get("invitacion") or r.get("id") or "").strip().lower().replace(" ", "_").replace("-", "_")
        if not inv_id:
            inv_id = f"guest_{r.get('id', '')}"
        groups.setdefault(inv_id, []).append(r)

    # Filter groups where NO guest in the group has a phone number
    no_phone_groups = {}
    for inv_id, recs in groups.items():
        if all(not str(r.get("telefono", "") or "").strip() for r in recs):
            dq = str(recs[0].get("dequien", "") or "").strip() or "Sin definir"
            no_phone_groups.setdefault(dq, []).append((inv_id, recs))

    sorted_origins = sorted(no_phone_groups.keys())
    total_no_phone = sum(len(g_list) for g_list in no_phone_groups.values())

    arg = (arg or "").strip().lower()

    # If no argument, show counts by origin with inline menu buttons
    if not arg:
        inline_keyboard = []
        row = []
        for orig in sorted_origins:
            count = len(no_phone_groups[orig])
            btn_text = f"{orig} ({count})" if count > 0 else f"{orig} (0)"
            row.append({"text": btn_text, "callback_data": f"sintelefono:{orig}"})
            if len(row) == 2:
                inline_keyboard.append(row)
                row = []
        if row:
            inline_keyboard.append(row)

        if total_no_phone > 0:
            inline_keyboard.append([{"text": f"📋 Todas ({total_no_phone})", "callback_data": "sintelefono:todos"}])

        send_message(
            chat_id,
            f"📱 <b>¿De quién querés ver tarjetas sin teléfono? ({total_no_phone} en total)</b>\n\n"
            f"Elegí una opción:",
            reply_markup={"inline_keyboard": inline_keyboard} if inline_keyboard else None
        )
        return

    selected_items = []
    if arg in ("todos", "all"):
        for orig in sorted_origins:
            selected_items.extend(no_phone_groups[orig])
        title = "todas las tarjetas"
    else:
        matched_groups = []
        # 1. Exact match on full dequien
        for orig in sorted_origins:
            if orig.lower() == arg:
                matched_groups.append((orig, no_phone_groups[orig]))
        # 2. Prefix or first-word match (e.g. 'celia' -> Celia Familia, Celia Amigos, Celia Oficina)
        if not matched_groups:
            for orig in sorted_origins:
                orig_lower = orig.lower()
                first_word = orig_lower.split()[0] if orig_lower else ""
                if orig_lower.startswith(arg) or first_word == arg:
                    matched_groups.append((orig, no_phone_groups[orig]))

        for orig, g_list in matched_groups:
            selected_items.extend(g_list)

        if len(matched_groups) == 1:
            title = matched_groups[0][0]
        elif len(matched_groups) > 1:
            title = ", ".join(mg[0] for mg in matched_groups)
        else:
            title = arg

    if not selected_items:
        send_message(chat_id, f"✅ No hay tarjetas sin teléfono para <b>{html.escape(title)}</b>.")
        return

    header = f"📱 <b>Tarjetas sin teléfono ({title}) - {len(selected_items)} tarjetas:</b>"
    items = []
    for inv_id, recs in selected_items:
        dq = str(recs[0].get("dequien", "") or "").strip()
        names = [f"{r.get('nombre', '')} {r.get('apellido', '')}".strip() for r in recs if f"{r.get('nombre', '')} {r.get('apellido', '')}".strip()]
        names_str = ", ".join(names) if names else inv_id
        url = str(recs[0].get("url", "") or "").strip()
        if not url:
            url = generate_invitation_url(inv_id)

        orig_label = f" ({dq})" if dq else ""
        items.append(f"💌 <b>{inv_id}</b>{orig_label}\n👥 {names_str}\n🔗 {url}")

    _send_chunked_messages(chat_id, header, items)


# ==============================================================
# 💳 Payment Management (/pagos)
# ==============================================================

ALLOWED_TELEGRAM_COMP_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}


def _parse_monto_int(raw_val) -> int:
    digits = re.sub(r"[^\d]", "", str(raw_val or ""))
    return int(digits) if digits else 0


def _format_monto_ar(amount: int) -> str:
    return f"${amount:,}".replace(",", ".")


def _get_paid_amount(rec: dict) -> int:
    raw = str(rec.get("pagado", "") or "").strip().lower()
    if raw in ("", "no", "false", "0"):
        return 0
    if raw in ("si", "sí", "pagado", "true"):
        monto = _parse_monto_int(rec.get("montotarjeta"))
        return monto if monto > 0 else 1
    return _parse_monto_int(raw)


def _get_remaining_amount(rec: dict) -> int:
    monto = _parse_monto_int(rec.get("montotarjeta"))
    paid = _get_paid_amount(rec)
    if monto > 0:
        return max(0, monto - paid)
    return 0 if paid > 0 else 0


def _is_paid_record(rec: dict) -> bool:
    paid = _get_paid_amount(rec)
    monto = _parse_monto_int(rec.get("montotarjeta"))
    if monto > 0:
        return paid >= monto
    return paid > 0


def _is_paying_guest(rec: dict) -> bool:
    monto = _parse_monto_int(rec.get("montotarjeta"))
    nivel = str(rec.get("nivel", "") or "").strip().lower()
    conf = str(rec.get("confirmacion", "") or "").strip().lower()
    return (monto > 0 or nivel == "reserva") and conf not in ("no", "rechazado")


def _parse_comprobante_urls(raw_val) -> list:
    if not raw_val:
        return []
    urls = []
    seen = set()
    for part in re.split(r"[\r\n,]+", str(raw_val)):
        u = part.strip()
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _get_group_comprobante_urls(recs: list) -> list:
    urls = []
    seen = set()
    for r in recs or []:
        for u in _parse_comprobante_urls(r.get("comprobante", "")):
            if u not in seen:
                seen.add(u)
                urls.append(u)
    return urls


def _comprobante_key_from_url(comp_url: str) -> str:
    parsed = urllib.parse.urlparse(str(comp_url or "").strip())
    base = os.path.basename(urllib.parse.unquote(parsed.path or ""))
    return base or str(comp_url or "").strip()


def _parse_montocomprobante_map(recs: list) -> dict:
    """Parses 'archivo: monto' lines (or plain single number fallback) from montocomprobante."""
    result = {}
    for r in recs or []:
        raw = str(r.get("montocomprobante", "") or "").strip()
        if not raw:
            continue
        for line in re.split(r"[\r\n]+", raw):
            part = line.strip()
            if not part:
                continue
            if ":" in part:
                key_part, val_part = part.rsplit(":", 1)
                key = _comprobante_key_from_url(key_part.strip())
                monto = _parse_monto_int(val_part)
                if key and monto > 0:
                    result[key] = monto
            else:
                monto = _parse_monto_int(part)
                if monto > 0:
                    result["__single__"] = monto
    return result


def _get_comprobante_monto(comp_url: str, comp_urls: list, monto_map: dict) -> int:
    key = _comprobante_key_from_url(comp_url)
    if key in monto_map:
        return monto_map[key]
    if comp_url in monto_map:
        return monto_map[comp_url]
    if len(comp_urls) == 1 and "__single__" in monto_map:
        return monto_map["__single__"]
    return 0


def _serialize_montocomprobante_map(comp_urls: list, monto_map: dict) -> str:
    lines = []
    for u in comp_urls or []:
        key = _comprobante_key_from_url(u)
        monto = _get_comprobante_monto(u, comp_urls, monto_map)
        if key and monto > 0:
            lines.append(f"{key}: {monto}")
    return "\n".join(lines)


def _append_comprobante_lines_and_buttons(
    lines: list,
    kb_rows: list,
    inv_id: str,
    recs: list,
    comp_urls: list,
    include_links: bool = True,
):
    if not comp_urls:
        return
    monto_map = _parse_montocomprobante_map(recs)
    confirmed_sum = 0
    for c_idx, u in enumerate(comp_urls, start=1):
        m = _get_comprobante_monto(u, comp_urls, monto_map)
        if m > 0:
            confirmed_sum += m
            status_txt = f"💰 {_format_monto_ar(m)}"
        else:
            status_txt = "⏳ Monto sin confirmar"
        if include_links:
            lines.append(f'🔗 <a href="{html.escape(u)}">Ver comprobante {c_idx}</a> — {status_txt}')
        else:
            lines.append(f"📎 Comprobante {c_idx} — {status_txt}")

    if confirmed_sum > 0:
        assigned_sum = sum(_get_paid_amount(r) for r in recs)
        avail_sum = max(0, confirmed_sum - assigned_sum)
        lines.append(
            f"🧾 Comprobantes: <b>{_format_monto_ar(confirmed_sum)}</b> · "
            f"Asignado: <b>{_format_monto_ar(assigned_sum)}</b> · "
            f"Disponible: <b>{_format_monto_ar(avail_sum)}</b>"
        )

    for c_idx, u in enumerate(comp_urls, start=1):
        m = _get_comprobante_monto(u, comp_urls, monto_map)
        view_label = f"👁️ Ver comprobante {c_idx}" if len(comp_urls) > 1 else "👁️ Ver comprobante"
        if m > 0:
            monto_label = f"✏️ Monto {c_idx} ({_format_monto_ar(m)})" if len(comp_urls) > 1 else f"✏️ Monto ({_format_monto_ar(m)})"
        else:
            monto_label = f"💵 Confirmar monto {c_idx}" if len(comp_urls) > 1 else "💵 Confirmar monto"
        kb_rows.append([
            {"text": view_label, "callback_data": f"pagos_ver:{inv_id[:46]}:{c_idx - 1}"},
            {"text": monto_label, "callback_data": f"pagos_comp_m:{inv_id[:46]}:{c_idx - 1}"},
        ])


def _group_records_by_invitacion(records: list) -> dict:
    groups = {}
    for r in records or []:
        inv_id = _normalize_slug(r.get("invitacion_id") or r.get("invitacion") or r.get("id") or "")
        if not inv_id:
            inv_id = f"guest_{r.get('id', '')}"
        groups.setdefault(inv_id, []).append(r)
    return groups


def _classify_pagos_groups(records: list):
    groups = _group_records_by_invitacion(records)
    with_comp_unconfirmed = []

    for inv_id, recs in sorted(groups.items()):
        comp_urls = _get_group_comprobante_urls(recs)
        if comp_urls:
            monto_map = _parse_montocomprobante_map(recs)
            has_unconfirmed_comp = any(_get_comprobante_monto(u, comp_urls, monto_map) <= 0 for u in comp_urls)
            unpaid_paying = [r for r in recs if _is_paying_guest(r) and not _is_paid_record(r)]
            _, _, available = _get_group_comprobantes_balance(recs)
            if has_unconfirmed_comp or (available > 0 and unpaid_paying):
                with_comp_unconfirmed.append((inv_id, recs, comp_urls, unpaid_paying))

    return with_comp_unconfirmed


def _format_guest_monto_line(r: dict) -> str:
    nombre = f"{r.get('nombre', '')} {r.get('apellido', '')}".strip() or "Invitado"
    monto = _parse_monto_int(r.get("montotarjeta"))
    monto_str = _format_monto_ar(monto) if monto > 0 else "$0"
    paid = _get_paid_amount(r)
    conf = str(r.get("confirmacion", "") or "").strip().lower()
    if _is_paid_record(r):
        status = "💰 Pagado"
    elif paid > 0 and monto > 0:
        faltan = max(0, monto - paid)
        status = f"🌓 Parcial ({_format_monto_ar(paid)} / faltan {_format_monto_ar(faltan)})"
    elif conf in ("no", "rechazado"):
        status = "🔴 No asiste"
    elif monto > 0 or str(r.get("nivel", "") or "").strip().lower() == "reserva":
        status = "Sin pagar"
    else:
        status = "💌 Invitado"
    return f"• {html.escape(nombre)} — <b>{monto_str}</b> · {status}"


def _handle_pagos_menu(chat_id: int | str, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    records = tbl.get_all() or []
    with_comp_unconfirmed = _classify_pagos_groups(records)

    reply_markup = {
        "inline_keyboard": [
            [{"text": f"⏳ Comprobantes por confirmar ({len(with_comp_unconfirmed)})", "callback_data": "pagos:por_confirmar"}],
            [{"text": "🔍 Buscar", "callback_data": "pagos:buscar"}],
            [{"text": "🏠 Menú", "callback_data": "cmd:menu"}],
        ]
    }
    send_message(
        chat_id,
        f"💳 <b>Gestión de Pagos</b>\n\n"
        f"• Comprobantes por confirmar: <b>{len(with_comp_unconfirmed)}</b> tarjetas",
        reply_markup=reply_markup,
    )


def _prompt_pagos_search(chat_id: int | str):
    with _lock:
        _user_states[str(chat_id)] = "awaiting_pagos_search"
    send_message(
        chat_id,
        "🔍 <b>Buscar para gestionar pago</b>\n\nEscribí un nombre, apellido o tarjeta para buscar:",
        reply_markup={"inline_keyboard": [[{"text": "❌ Cancelar", "callback_data": "cmd:pagos"}]]},
    )


def _append_payment_action_buttons(
    kb_rows: list,
    inv_id: str,
    recs: list,
    confirm_candidates: list,
    include_unconfirm: bool = False,
):
    total_comp, _, available = _get_group_comprobantes_balance(recs)
    can_confirm = (total_comp == 0) or (available > 0)

    if can_confirm:
        for r in confirm_candidates:
            gid = str(r.get("id", "") or "").strip()
            if not gid:
                continue
            nombre = f"{r.get('nombre', '')} {r.get('apellido', '')}".strip() or "Invitado"
            monto = _parse_monto_int(r.get("montotarjeta"))
            paid = _get_paid_amount(r)
            faltan = _get_remaining_amount(r)
            monto_str = _format_monto_ar(monto) if monto > 0 else "$0"
            if total_comp > 0 and faltan > 0 and available < faltan:
                btn_txt = f"🌓 Pago parcial: {nombre[:16]} (+{_format_monto_ar(available)})"
            elif paid > 0 and faltan > 0:
                btn_txt = f"💰 Confirmar pago: {nombre[:16]} (faltan {_format_monto_ar(faltan)})"
            else:
                btn_txt = f"💰 Confirmar pago: {nombre[:20]} ({monto_str})"
            kb_rows.append([{"text": btn_txt, "callback_data": f"pagos_ok_p:{gid}"}])

        unpaid_paying = [r for r in recs if _is_paying_guest(r) and not _is_paid_record(r)]
        total_unpaid = sum(_get_remaining_amount(r) for r in unpaid_paying)
        if len(unpaid_paying) >= 2 and (total_comp == 0 or (total_unpaid > 0 and available >= total_unpaid)):
            total_str = _format_monto_ar(total_unpaid) if total_unpaid > 0 else "$0"
            kb_rows.append([{"text": f"💰 Confirmar todos ({total_str})", "callback_data": f"pagos_ok_g:{inv_id[:50]}"}])

    if include_unconfirm:
        for r in recs:
            if _get_paid_amount(r) > 0:
                gid = str(r.get("id", "") or "").strip()
                if not gid:
                    continue
                nombre = f"{r.get('nombre', '')} {r.get('apellido', '')}".strip() or "Invitado"
                kb_rows.append([{"text": f"↩️ Desmarcar pago: {nombre[:22]}", "callback_data": f"pagos_unok_p:{gid}"}])


def _handle_pagos_search(chat_id: int | str, query: str, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    norm_q = _normalize_search_text(query)
    if not norm_q:
        _prompt_pagos_search(chat_id)
        return

    records = tbl.get_all() or []
    groups = _group_records_by_invitacion(records)
    matched_groups = []
    for inv_id, recs in sorted(groups.items()):
        group_match = norm_q in _normalize_search_text(inv_id) or norm_q in _normalize_search_text(inv_id.replace("_", " "))
        guest_match = False
        if not group_match:
            for r in recs:
                full_name = f"{r.get('nombre', '')} {r.get('apellido', '')}".strip()
                fields = (
                    full_name,
                    r.get("nombre", ""),
                    r.get("apellido", ""),
                    r.get("invitacion", ""),
                    r.get("de_quien", ""),
                )
                if any(norm_q in _normalize_search_text(f) for f in fields):
                    guest_match = True
                    break
        if group_match or guest_match:
            matched_groups.append((inv_id, recs))

    if not matched_groups:
        send_message(
            chat_id,
            f"🔍 No se encontraron invitados o tarjetas para <b>{html.escape(query)}</b>.",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "🔍 Buscar otro", "callback_data": "pagos:buscar"}],
                    [{"text": "💳 Volver a Pagos", "callback_data": "cmd:pagos"}],
                ]
            },
        )
        return

    shown = matched_groups[:10]
    if len(matched_groups) > 10:
        send_message(
            chat_id,
            f"🔍 Mostrando 10 de <b>{len(matched_groups)}</b> tarjetas coincidentes con <b>{html.escape(query)}</b>:",
        )

    for idx, (inv_id, recs) in enumerate(shown):
        group_title = inv_id.replace("_", " ").title()
        comp_urls = _get_group_comprobante_urls(recs)
        comp_info = f" · 📎 {len(comp_urls)} comp." if comp_urls else " · Sin comp."
        lines = [f"💳 <b>{html.escape(group_title)}</b>{comp_info}"]
        for r in recs:
            lines.append(_format_guest_monto_line(r))

        kb_rows = []
        search_candidates = [
            r for r in recs
            if not _is_paid_record(r) and str(r.get("confirmacion", "") or "").strip().lower() not in ("no", "rechazado")
        ]
        _append_payment_action_buttons(kb_rows, inv_id, recs, search_candidates, include_unconfirm=True)

        _append_comprobante_lines_and_buttons(lines, kb_rows, inv_id, recs, comp_urls, include_links=False)

        kb_rows.append([{"text": f"📎 Subir comprobante ({group_title[:22]})", "callback_data": f"pagos_up:{inv_id[:52]}"}])

        if idx == len(shown) - 1:
            kb_rows.append([
                {"text": "🔍 Buscar otro", "callback_data": "pagos:buscar"},
                {"text": "💳 Volver a Pagos", "callback_data": "cmd:pagos"},
            ])

        send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": kb_rows})


def _handle_pagos_por_confirmar(chat_id: int | str, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    records = tbl.get_all() or []
    with_comp_unconfirmed = _classify_pagos_groups(records)

    back_markup = {"inline_keyboard": [[{"text": "💳 Volver a Pagos", "callback_data": "cmd:pagos"}]]}
    if not with_comp_unconfirmed:
        send_message(chat_id, "✅ No hay comprobantes pendientes de confirmación.", reply_markup=back_markup)
        return

    for idx, (inv_id, recs, comp_urls, unpaid_paying) in enumerate(with_comp_unconfirmed):
        group_title = inv_id.replace("_", " ").title()
        lines = [f"⏳ <b>{html.escape(group_title)}</b>"]
        for r in recs:
            lines.append(_format_guest_monto_line(r))

        kb_rows = []
        _append_comprobante_lines_and_buttons(lines, kb_rows, inv_id, recs, comp_urls, include_links=True)
        _append_payment_action_buttons(kb_rows, inv_id, recs, unpaid_paying, include_unconfirm=False)

        if idx == len(with_comp_unconfirmed) - 1:
            kb_rows.append([{"text": "💳 Volver a Pagos", "callback_data": "cmd:pagos"}])

        send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": kb_rows})


def _resolve_comprobante_bytes(comp_url: str) -> tuple[bytes | None, str]:
    """Resolves a comprobante URL to raw bytes, checking local COMPROBANTES_DIR first."""
    parsed = urllib.parse.urlparse(comp_url or "")
    filename = os.path.basename(urllib.parse.unquote(parsed.path or "")) or "comprobante"
    comp_dir = os.environ.get("COMPROBANTES_DIR") or TELEGRAM_COMPROBANTES_DIR
    local_path = os.path.join(comp_dir, filename)
    if filename and os.path.isfile(local_path):
        try:
            with open(local_path, "rb") as f:
                return f.read(), filename
        except Exception as e:
            logger.warning(f"Failed reading local comprobante {local_path}: {e}")

    if comp_url and comp_url.startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(comp_url, method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read(), filename
        except Exception as e:
            logger.warning(f"Failed downloading comprobante {comp_url}: {e}")

    return None, filename


def _render_pdf_to_png_pages(pdf_bytes: bytes, max_pages: int = 5) -> list[bytes]:
    """Converts up to max_pages of a PDF to PNG bytes using PyMuPDF (fitz)."""
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF (fitz) is not installed; falling back to sendDocument for PDF.")
        return []

    pages: list[bytes] = []
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for i in range(min(len(doc), max_pages)):
            pix = doc[i].get_pixmap(dpi=150)
            pages.append(pix.tobytes("png"))
    except Exception as e:
        logger.warning(f"Failed rendering PDF to PNG: {e}")
        return []
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
    return pages


def _send_comprobante_preview(chat_id: int | str, comp_url: str, caption: str = "", reply_markup: dict = None) -> bool:
    """Sends a comprobante as image(s) in Telegram, converting PDFs to PNG with document fallback."""
    raw_bytes, filename = _resolve_comprobante_bytes(comp_url)
    if not raw_bytes:
        send_message(chat_id, f"❌ No se pudo obtener el comprobante: {html.escape(comp_url or '')}", reply_markup=reply_markup)
        return False

    ext = os.path.splitext(filename)[1].lower()
    is_pdf = ext == ".pdf" or raw_bytes.startswith(b"%PDF")
    if is_pdf:
        pages = _render_pdf_to_png_pages(raw_bytes)
        if pages:
            base_name = os.path.splitext(filename)[0] or "comprobante"
            ok_all = True
            for p_idx, png_bytes in enumerate(pages, start=1):
                if len(pages) > 1:
                    page_cap = f"{caption} (pág. {p_idx}/{len(pages)})" if caption else f"Pág. {p_idx}/{len(pages)}"
                else:
                    page_cap = caption
                is_last = p_idx == len(pages)
                ok = send_photo_bytes(
                    chat_id,
                    png_bytes,
                    filename=f"{base_name}_p{p_idx}.png",
                    caption=page_cap,
                    reply_markup=reply_markup if is_last else None,
                )
                ok_all = ok_all and ok
            return ok_all
        doc_name = filename if ext == ".pdf" else f"{filename}.pdf"
        return send_document_bytes(chat_id, raw_bytes, filename=doc_name, caption=caption, reply_markup=reply_markup)

    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif") or not ext:
        return send_photo_bytes(chat_id, raw_bytes, filename=filename, caption=caption, reply_markup=reply_markup)

    return send_document_bytes(chat_id, raw_bytes, filename=filename, caption=caption, reply_markup=reply_markup)


def _handle_ver_comprobante(chat_id: int | str, inv_prefix: str, idx_str: str, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    records = tbl.get_all() or []
    groups = _group_records_by_invitacion(records)
    inv_id = next((k for k in groups if k == inv_prefix or k.startswith(inv_prefix)), None)
    if not inv_id:
        send_message(chat_id, "❌ No se encontró la invitación.")
        return

    comp_urls = _get_group_comprobante_urls(groups[inv_id])
    if not comp_urls:
        send_message(chat_id, "❌ Esta invitación no tiene comprobantes.")
        return

    try:
        idx = int(idx_str)
    except (TypeError, ValueError):
        idx = -1

    if idx < 0 or idx >= len(comp_urls):
        resolved_idx = len(comp_urls) - 1
    else:
        resolved_idx = idx
    comp_url = comp_urls[resolved_idx]
    label_idx = resolved_idx + 1

    monto_map = _parse_montocomprobante_map(groups[inv_id])
    curr_monto = _get_comprobante_monto(comp_url, comp_urls, monto_map)
    group_title = inv_id.replace("_", " ").title()
    caption = f"🧾 {group_title} — Comprobante {label_idx}/{len(comp_urls)}"
    if curr_monto > 0:
        caption += f" (💰 {_format_monto_ar(curr_monto)})"
        btn_label = f"✏️ Editar monto ({_format_monto_ar(curr_monto)})"
    else:
        btn_label = "💵 Confirmar monto"

    markup = {
        "inline_keyboard": [
            [{"text": btn_label, "callback_data": f"pagos_comp_m:{inv_id[:46]}:{resolved_idx}"}]
        ]
    }
    _send_comprobante_preview(chat_id, comp_url, caption=caption, reply_markup=markup)


def _resolve_comp_target(inv_prefix: str, idx_str: str, table):
    tbl = table or _table_ref
    if not tbl:
        return None, None, None, None, "Error: Base de datos no disponible."
    records = tbl.get_all() or []
    groups = _group_records_by_invitacion(records)
    inv_id = next((k for k in groups if k == inv_prefix or k.startswith(inv_prefix)), None)
    if not inv_id:
        return tbl, None, None, None, "❌ No se encontró la invitación."
    recs = groups[inv_id]
    comp_urls = _get_group_comprobante_urls(recs)
    if not comp_urls:
        return tbl, inv_id, recs, None, "❌ Esta invitación no tiene comprobantes."
    try:
        idx = int(idx_str)
    except (TypeError, ValueError):
        idx = -1
    resolved_idx = (len(comp_urls) - 1) if (idx < 0 or idx >= len(comp_urls)) else idx
    return tbl, inv_id, recs, (comp_urls, resolved_idx), None


def _prompt_comprobante_monto(chat_id: int | str, inv_prefix: str, idx_str: str, table):
    tbl, inv_id, recs, comp_info, err = _resolve_comp_target(inv_prefix, idx_str, table)
    if err:
        send_message(chat_id, err)
        return

    comp_urls, resolved_idx = comp_info
    with _lock:
        _user_states[str(chat_id)] = f"awaiting_comp_monto:{inv_id}:{resolved_idx}"

    suggested = []
    seen_amt = set()
    for r in recs:
        if _is_paying_guest(r) or _is_paid_record(r):
            amt = _parse_monto_int(r.get("montotarjeta"))
            if amt > 0 and amt not in seen_amt:
                seen_amt.add(amt)
                suggested.append(amt)
    paying_recs = [r for r in recs if _is_paying_guest(r) or _is_paid_record(r)]
    if len(paying_recs) >= 2:
        total_amt = sum(_parse_monto_int(r.get("montotarjeta")) for r in paying_recs)
        if total_amt > 0 and total_amt not in seen_amt:
            seen_amt.add(total_amt)
            suggested.append(total_amt)

    kb_rows = []
    if suggested:
        row = []
        for amt in suggested[:4]:
            row.append({
                "text": f"💰 {_format_monto_ar(amt)}",
                "callback_data": f"pagos_comp_set:{inv_id[:30]}:{resolved_idx}:{amt}",
            })
            if len(row) == 2:
                kb_rows.append(row)
                row = []
        if row:
            kb_rows.append(row)
    kb_rows.append([{"text": "❌ Cancelar", "callback_data": "cmd:pagos"}])

    group_title = inv_id.replace("_", " ").title()
    send_message(
        chat_id,
        f"💵 <b>Confirmar monto — Comprobante {resolved_idx + 1}/{len(comp_urls)}</b>\n"
        f"💌 Tarjeta: <b>{html.escape(group_title)}</b>\n\n"
        f"Escribí el monto (ej: <code>105000</code>) o elegí una opción:",
        reply_markup={"inline_keyboard": kb_rows},
    )


def _apply_comprobante_monto(chat_id: int | str, inv_prefix: str, idx_str: str, amount_val, table):
    monto = _parse_monto_int(amount_val)
    if monto <= 0:
        send_message(chat_id, "⚠️ Ingresá un monto válido mayor a 0 (ej: <code>105000</code>).")
        return

    tbl, inv_id, recs, comp_info, err = _resolve_comp_target(inv_prefix, idx_str, table)
    if err:
        send_message(chat_id, err)
        return

    comp_urls, resolved_idx = comp_info
    target_url = comp_urls[resolved_idx]
    target_key = _comprobante_key_from_url(target_url)

    monto_map = _parse_montocomprobante_map(recs)
    monto_map[target_key] = monto
    combined = _serialize_montocomprobante_map(comp_urls, monto_map)

    for g in recs:
        tbl.update_record(g["id"], {"montocomprobante": combined})

    _log(f"[PAGOS:COMP_MONTO] chat_id={chat_id} invitacion={inv_id} file={target_key} monto={monto}")

    total_comp = sum(_get_comprobante_monto(u, comp_urls, monto_map) for u in comp_urls)
    paying_targets = [r for r in recs if _is_paying_guest(r)]
    expected_total = sum(_parse_monto_int(r.get("montotarjeta")) for r in paying_targets)
    unpaid_targets = [r for r in paying_targets if not _is_paid_record(r)]

    auto_note = ""
    if expected_total > 0 and total_comp >= expected_total and unpaid_targets:
        auto_paid_names = []
        for r in unpaid_targets:
            m_tarjeta = _parse_monto_int(r.get("montotarjeta"))
            if m_tarjeta > 0:
                tbl.update_record(r["id"], {"pagado": str(m_tarjeta)})
                auto_paid_names.append(f"{r.get('nombre', '')} {r.get('apellido', '')}".strip() or "Invitado")
        if auto_paid_names:
            _log(f"[PAGOS:AUTO_DISTRIBUTE] chat_id={chat_id} invitacion={inv_id} total={total_comp} guests={auto_paid_names}")
            auto_note = (
                f"\n\n🎉 ¡Los comprobantes cubren el total (<b>{_format_monto_ar(expected_total)}</b>)! "
                f"Se marcó automáticamente como pagado a: <b>{html.escape(', '.join(auto_paid_names))}</b>."
            )

    group_title = inv_id.replace("_", " ").title()
    header_line = (
        f"✅ Monto confirmado para <b>Comprobante {resolved_idx + 1}/{len(comp_urls)}</b> "
        f"de <b>{html.escape(group_title)}</b>: <b>{_format_monto_ar(monto)}</b>{auto_note}"
    )

    kb_rows = []
    if not auto_note and unpaid_targets:
        header_line = _append_remaining_assignment_ui(header_line, kb_rows, inv_id, recs, unpaid_targets)

    kb_rows.append([{"text": "⏳ Ver pendientes por confirmar", "callback_data": "pagos:por_confirmar"}])
    kb_rows.append([{"text": "💳 Menú Pagos", "callback_data": "cmd:pagos"}])

    send_message(
        chat_id,
        header_line,
        reply_markup={"inline_keyboard": kb_rows},
    )


def _append_remaining_assignment_ui(
    header_line: str,
    kb_rows: list,
    inv_id: str,
    recs: list,
    remaining_targets: list,
) -> str:
    """If confirmed comprobante balance > 0 and unpaid guests remain, appends status lines and assign buttons."""
    total_comp, assigned_sum, avail_sum = _get_group_comprobantes_balance(recs)
    if total_comp > 0 and avail_sum > 0 and remaining_targets:
        lines = [header_line, ""]
        for r in recs:
            lines.append(_format_guest_monto_line(r))
        lines.append(
            f"🧾 Comprobantes: <b>{_format_monto_ar(total_comp)}</b> · "
            f"Asignado: <b>{_format_monto_ar(assigned_sum)}</b> · "
            f"Disponible: <b>{_format_monto_ar(avail_sum)}</b>"
        )
        _append_payment_action_buttons(kb_rows, inv_id, recs, remaining_targets, include_unconfirm=False)
        return "\n".join(lines)
    return header_line


def _get_group_comprobantes_balance(recs: list) -> tuple[int, int, int]:
    """Returns (total_comprobantes_confirmados, total_asignado_a_invitados, saldo_disponible)."""
    comp_urls = _get_group_comprobante_urls(recs)
    monto_map = _parse_montocomprobante_map(recs)
    total_comp = sum(_get_comprobante_monto(u, comp_urls, monto_map) for u in comp_urls)
    total_assigned = sum(_get_paid_amount(r) for r in recs)
    available = max(0, total_comp - total_assigned)
    return total_comp, total_assigned, available


def _handle_confirmar_pago_persona(chat_id: int | str, guest_id: str, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    records = tbl.get_all() or []
    target = next((r for r in records if str(r.get("id", "") or "").strip() == str(guest_id).strip()), None)
    if not target:
        send_message(chat_id, "⚠️ No se encontró el invitado indicado.")
        return

    inv_id = _normalize_slug(target.get("invitacion_id") or target.get("invitacion") or target.get("id") or "")
    groups = _group_records_by_invitacion(records)
    group_recs = groups.get(inv_id) or [target]

    nombre = f"{target.get('nombre', '')} {target.get('apellido', '')}".strip() or "Invitado"
    monto_tarjeta = _parse_monto_int(target.get("montotarjeta"))
    paid_actual = _get_paid_amount(target)
    faltan = _get_remaining_amount(target)

    if _is_paid_record(target):
        send_message(chat_id, f"✅ <b>{html.escape(nombre)}</b> ya figura pagado completo.")
        return

    total_comp, total_assigned, available = _get_group_comprobantes_balance(group_recs)
    if total_comp > 0:
        if available <= 0:
            send_message(
                chat_id,
                f"⚠️ Ya se asignó todo el saldo de los comprobantes confirmados "
                f"(<b>{_format_monto_ar(total_assigned)}</b> / <b>{_format_monto_ar(total_comp)}</b>).\n"
                f"Confirmá otro monto de comprobante o desmarcá a otro invitado primero.",
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "⏳ Ver pendientes por confirmar", "callback_data": "pagos:por_confirmar"}],
                        [{"text": "💳 Menú Pagos", "callback_data": "cmd:pagos"}],
                    ]
                },
            )
            return
        to_credit = min(faltan, available) if faltan > 0 else available
    else:
        to_credit = faltan if faltan > 0 else monto_tarjeta

    new_paid = paid_actual + to_credit if to_credit > 0 else (monto_tarjeta or 1)
    tbl.update_record(target["id"], {"pagado": str(new_paid)})
    target["pagado"] = str(new_paid)
    _log(f"[PAGOS:CONFIRM_PERSON] chat_id={chat_id} guest_id={guest_id} nombre={nombre!r} monto={_format_monto_ar(new_paid)}")

    if monto_tarjeta > 0 and new_paid < monto_tarjeta:
        msg_txt = (
            f"🌓 Pago parcial registrado para <b>{html.escape(nombre)}</b>: "
            f"+<b>{_format_monto_ar(to_credit)}</b> (total <b>{_format_monto_ar(new_paid)}</b> / {_format_monto_ar(monto_tarjeta)})."
        )
    else:
        msg_txt = f"✅ Pago confirmado para <b>{html.escape(nombre)}</b> (<b>{_format_monto_ar(new_paid)}</b>)."

    kb_rows = []
    remaining_targets = [r for r in group_recs if _is_paying_guest(r) and not _is_paid_record(r)]
    msg_txt = _append_remaining_assignment_ui(msg_txt, kb_rows, inv_id, group_recs, remaining_targets)
    kb_rows.append([{"text": "⏳ Ver pendientes por confirmar", "callback_data": "pagos:por_confirmar"}])
    kb_rows.append([{"text": "🔍 Buscar otro", "callback_data": "pagos:buscar"}])
    kb_rows.append([{"text": "💳 Menú Pagos", "callback_data": "cmd:pagos"}])

    send_message(
        chat_id,
        msg_txt,
        reply_markup={"inline_keyboard": kb_rows},
    )


def _handle_desmarcar_pago_persona(chat_id: int | str, guest_id: str, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    records = tbl.get_all() or []
    target = next((r for r in records if str(r.get("id", "") or "").strip() == str(guest_id).strip()), None)
    if not target:
        send_message(chat_id, "⚠️ No se encontró el invitado indicado.")
        return

    tbl.update_record(target["id"], {"pagado": ""})
    nombre = f"{target.get('nombre', '')} {target.get('apellido', '')}".strip() or "Invitado"
    _log(f"[PAGOS:UNCONFIRM_PERSON] chat_id={chat_id} guest_id={guest_id} nombre={nombre!r}")

    send_message(
        chat_id,
        f"↩️ Pago desmarcado para <b>{html.escape(nombre)}</b>.",
        reply_markup={
            "inline_keyboard": [
                [{"text": "🔍 Buscar otro", "callback_data": "pagos:buscar"}],
                [{"text": "💳 Menú Pagos", "callback_data": "cmd:pagos"}],
            ]
        },
    )


def _handle_confirmar_pago_grupo(chat_id: int | str, inv_id_prefix: str, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    records = tbl.get_all() or []
    groups = _group_records_by_invitacion(records)
    matched_inv = next((k for k in groups if k == inv_id_prefix or k.startswith(inv_id_prefix)), None)
    if not matched_inv:
        send_message(chat_id, "⚠️ No se encontró la tarjeta indicada.")
        return

    recs = groups[matched_inv]
    targets = [r for r in recs if _is_paying_guest(r) and not _is_paid_record(r)]
    if not targets:
        targets = [r for r in recs if str(r.get("confirmacion", "") or "").strip().lower() not in ("no", "rechazado") and not _is_paid_record(r)]

    total_comp, _, available = _get_group_comprobantes_balance(recs)
    if total_comp > 0 and available <= 0:
        send_message(
            chat_id,
            f"⚠️ Ya se asignó todo el saldo de los comprobantes confirmados (<b>{_format_monto_ar(total_comp)}</b>).",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "⏳ Ver pendientes por confirmar", "callback_data": "pagos:por_confirmar"}],
                    [{"text": "💳 Menú Pagos", "callback_data": "cmd:pagos"}],
                ]
            },
        )
        return

    confirmed_names = []
    total_credited = 0
    remaining_avail = available
    for r in targets:
        monto_tarjeta = _parse_monto_int(r.get("montotarjeta"))
        paid_actual = _get_paid_amount(r)
        faltan = _get_remaining_amount(r)
        if total_comp > 0:
            if remaining_avail <= 0:
                break
            to_credit = min(faltan, remaining_avail) if faltan > 0 else remaining_avail
            remaining_avail -= to_credit
        else:
            to_credit = faltan if faltan > 0 else monto_tarjeta

        new_paid = paid_actual + to_credit if to_credit > 0 else (monto_tarjeta or 1)
        tbl.update_record(r["id"], {"pagado": str(new_paid)})
        nombre = f"{r.get('nombre', '')} {r.get('apellido', '')}".strip() or "Invitado"
        total_credited += to_credit
        if monto_tarjeta > 0 and new_paid < monto_tarjeta:
            confirmed_names.append(f"{nombre} (parcial {_format_monto_ar(new_paid)}/{_format_monto_ar(monto_tarjeta)})")
        else:
            confirmed_names.append(f"{nombre} ({_format_monto_ar(new_paid)})" if new_paid > 0 else nombre)

    _log(f"[PAGOS:CONFIRM_GROUP] chat_id={chat_id} invitacion={matched_inv} count={len(confirmed_names)} total={total_credited}")
    group_title = matched_inv.replace("_", " ").title()
    summary = ", ".join(confirmed_names) if confirmed_names else "sin pendientes"
    send_message(
        chat_id,
        f"✅ Pago confirmado para <b>{html.escape(group_title)}</b>:\n{html.escape(summary)}",
        reply_markup={
            "inline_keyboard": [
                [{"text": "⏳ Ver pendientes por confirmar", "callback_data": "pagos:por_confirmar"}],
                [{"text": "💳 Menú Pagos", "callback_data": "cmd:pagos"}],
            ]
        },
    )


def _prompt_comprobante_upload(chat_id: int | str, inv_id_prefix: str, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    records = tbl.get_all() or []
    groups = _group_records_by_invitacion(records)
    matched_inv = next((k for k in groups if k == inv_id_prefix or k.startswith(inv_id_prefix)), None)
    if not matched_inv:
        send_message(chat_id, "⚠️ No se encontró la tarjeta indicada.")
        return

    with _lock:
        _user_states[str(chat_id)] = f"awaiting_comprobante:{matched_inv}"

    group_title = matched_inv.replace("_", " ").title()
    lines = [f"📎 Enviá la <b>foto o archivo PDF</b> del comprobante para <b>{html.escape(group_title)}</b>:"]
    for r in groups[matched_inv]:
        lines.append(_format_guest_monto_line(r))

    send_message(
        chat_id,
        "\n".join(lines),
        reply_markup={"inline_keyboard": [[{"text": "❌ Cancelar", "callback_data": "cmd:cancel"}]]},
    )


def _download_telegram_file(file_id: str, safe_slug: str, orig_filename: str = "") -> tuple | None:
    res = call_telegram_api("getFile", {"file_id": file_id})
    if not res or not res.get("ok"):
        return None
    file_path = (res.get("result") or {}).get("file_path") or ""
    if not file_path:
        return None

    ext = os.path.splitext(orig_filename or file_path)[1].lower()
    if not ext:
        ext = ".jpg"
    if ext not in ALLOWED_TELEGRAM_COMP_EXTS:
        raise ValueError("Formato no permitido. Enviá PDF o imagen (JPG, PNG, WEBP).")

    url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(url, timeout=35) as resp:
        file_bytes = resp.read()

    comp_dir = os.environ.get("COMPROBANTES_DIR") or TELEGRAM_COMPROBANTES_DIR
    os.makedirs(comp_dir, exist_ok=True)
    filename = f"comprobante_{safe_slug}_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    dest = os.path.join(comp_dir, filename)
    with open(dest, "wb") as f:
        f.write(file_bytes)

    base_site = (BASE_URL or "https://nos.vamos.acas.ar").rstrip("/")
    return filename, f"{base_site}/comprobantes/{filename}"


def _handle_telegram_comprobante_upload(chat_id: int | str, inv_id: str, msg: dict, table):
    tbl = table or _table_ref
    if not tbl:
        send_message(chat_id, "Error: Base de datos no disponible.")
        return

    file_id = ""
    orig_filename = ""
    if msg.get("photo"):
        photos = msg["photo"]
        if isinstance(photos, list) and photos:
            file_id = photos[-1].get("file_id", "")
            orig_filename = "comprobante.jpg"
    elif msg.get("document"):
        doc = msg["document"]
        file_id = doc.get("file_id", "")
        orig_filename = doc.get("file_name", "") or "comprobante.pdf"

    if not file_id:
        send_message(
            chat_id,
            "⚠️ Por favor enviá una <b>foto</b> o un archivo <b>PDF/imagen</b> del comprobante.",
            reply_markup={"inline_keyboard": [[{"text": "❌ Cancelar", "callback_data": "cmd:cancel"}]]},
        )
        return

    safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", inv_id)
    try:
        downloaded = _download_telegram_file(file_id, safe_slug, orig_filename)
    except ValueError as ve:
        send_message(chat_id, f"⚠️ {html.escape(str(ve))}")
        return
    except Exception as e:
        _log(f"[PAGOS:UPLOAD_ERROR] invitacion={inv_id} err={e}")
        send_message(chat_id, "❌ Error descargando el comprobante desde Telegram.")
        return

    if not downloaded:
        send_message(chat_id, "❌ No se pudo obtener el archivo desde Telegram.")
        return

    filename, comp_url = downloaded
    records = tbl.get_all() or []
    groups = _group_records_by_invitacion(records)
    guests = groups.get(inv_id) or []

    existing_urls = _get_group_comprobante_urls(guests)
    updated_urls = existing_urls + [comp_url]
    combined = "\n".join(updated_urls)

    for g in guests:
        tbl.update_record(g["id"], {"comprobante": combined})

    with _lock:
        _user_states.pop(str(chat_id), None)

    _log(f"[PAGOS:UPLOAD] chat_id={chat_id} invitacion={inv_id} file={filename}")
    group_title = inv_id.replace("_", " ").title()
    send_message(
        chat_id,
        f"✅ Comprobante guardado para <b>{html.escape(group_title)}</b>\n"
        f'🔗 <a href="{html.escape(comp_url)}">Ver archivo</a>',
        reply_markup={"inline_keyboard": [[{"text": "💳 Volver a Pagos", "callback_data": "cmd:pagos"}]]},
    )


def _handle_update(update: dict, table=None):
    # 0. Handle callback_query from inline keyboards
    if "callback_query" in update:
        cq = update["callback_query"]
        cq_id = cq.get("id")
        cq_data = (cq.get("data") or "").strip()
        cq_msg = cq.get("message") or {}
        cq_chat = cq_msg.get("chat") or {}
        cq_chat_id = cq_chat.get("id")

        if cq_id:
            call_telegram_api("answerCallbackQuery", {"callback_query_id": cq_id})

        if not cq_chat_id:
            return

        if not is_subscribed(cq_chat_id):
            send_message(cq_chat_id, "🔒 <b>Acceso no autorizado</b>")
            return

        cq_user = cq.get("from") or {}
        cq_user_name = cq_user.get("first_name") or cq_user.get("username") or "Admin"
        _log(f"[CALLBACK] chat_id={cq_chat_id} user={cq_user_name} data={cq_data}")

        with _lock:
            _user_states.pop(str(cq_chat_id), None)

        if cq_data == "cmd:status":
            stats_text = _get_wedding_stats(table or _table_ref)
            send_message(cq_chat_id, stats_text)
            return

        if cq_data == "cmd:send":
            _handle_send_command(cq_chat_id, "", table or _table_ref)
            return

        if cq_data == "cmd:sintelefono":
            _handle_sintelefono_command(cq_chat_id, "", table or _table_ref)
            return

        if cq_data == "cmd:pagos":
            _handle_pagos_menu(cq_chat_id, table or _table_ref)
            return

        if cq_data == "pagos:por_confirmar":
            _handle_pagos_por_confirmar(cq_chat_id, table or _table_ref)
            return

        if cq_data == "pagos:buscar":
            _prompt_pagos_search(cq_chat_id)
            return

        if cq_data.startswith("pagos_up:"):
            inv_prefix = cq_data[len("pagos_up:"):].strip()
            _prompt_comprobante_upload(cq_chat_id, inv_prefix, table or _table_ref)
            return

        if cq_data.startswith("pagos_ok_p:"):
            gid = cq_data[len("pagos_ok_p:"):].strip()
            _handle_confirmar_pago_persona(cq_chat_id, gid, table or _table_ref)
            return

        if cq_data.startswith("pagos_unok_p:"):
            gid = cq_data[len("pagos_unok_p:"):].strip()
            _handle_desmarcar_pago_persona(cq_chat_id, gid, table or _table_ref)
            return

        if cq_data.startswith("pagos_ok_g:"):
            inv_prefix = cq_data[len("pagos_ok_g:"):].strip()
            _handle_confirmar_pago_grupo(cq_chat_id, inv_prefix, table or _table_ref)
            return

        if cq_data.startswith("pagos_ver:"):
            rest = cq_data[len("pagos_ver:"):].strip()
            inv_prefix, idx_str = rest.rsplit(":", 1) if ":" in rest else (rest, "-1")
            _handle_ver_comprobante(cq_chat_id, inv_prefix.strip(), idx_str.strip(), table or _table_ref)
            return

        if cq_data.startswith("pagos_comp_m:"):
            rest = cq_data[len("pagos_comp_m:"):].strip()
            inv_prefix, idx_str = rest.rsplit(":", 1) if ":" in rest else (rest, "-1")
            _prompt_comprobante_monto(cq_chat_id, inv_prefix.strip(), idx_str.strip(), table or _table_ref)
            return

        if cq_data.startswith("pagos_comp_set:"):
            rest = cq_data[len("pagos_comp_set:"):].strip()
            parts = rest.rsplit(":", 2)
            if len(parts) == 3:
                _apply_comprobante_monto(cq_chat_id, parts[0].strip(), parts[1].strip(), parts[2].strip(), table or _table_ref)
            return

        if cq_data in ("cmd:menu", "cmd:cancel"):
            _send_main_menu(cq_chat_id, cq_user_name)
            return

        if cq_data.startswith("send:"):
            target_dq = cq_data[len("send:"):].strip()
            _handle_send_command(cq_chat_id, target_dq, table or _table_ref)
            return

        if cq_data.startswith("sintelefono:"):
            target_dq = cq_data[len("sintelefono:"):].strip()
            _handle_sintelefono_command(cq_chat_id, target_dq, table or _table_ref)
            return
        return

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    has_media = bool(msg.get("photo") or msg.get("document"))
    from_user = msg.get("from") or {}
    user_name = from_user.get("first_name") or from_user.get("username") or "Admin"

    if not chat_id or (not text and not has_media):
        return

    parts = text.split(maxsplit=1)
    raw_cmd = parts[0].lower().split("@")[0] if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    # 1. /start command
    if raw_cmd == "/start":
        provided_token = arg

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
                f"• /send &lt;origen&gt; - Links de WhatsApp (ej. /send Celia)\n"
                f"• /sintelefono - Ver tarjetas que no tienen teléfono\n"
                f"• /stop - Desactivar notificaciones"
            )
            send_message(chat_id, reply)
        else:
            if is_subscribed(chat_id):
                send_message(
                    chat_id,
                    f"👋 Hola <b>{user_name}</b>, ya estás registrado para recibir notificaciones.\n\n"
                    f"• /status - Ver estado de invitados\n"
                    f"• /send &lt;origen&gt; - Links de WhatsApp (ej. /send Celia)\n"
                    f"• /sintelefono - Ver tarjetas que no tienen teléfono\n"
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
        _log(f"[AUTH:DENIED] chat_id={chat_id} user={user_name} text={text!r}")
        send_message(
            chat_id,
            "🔒 <b>Acceso no autorizado</b>\n\n"
            "Por favor ingresá usando el enlace de autorización."
        )
        return

    _log(f"[MSG] chat_id={chat_id} user={user_name} text={text!r}")

    # 2b. Check active conversational state for non-command text or media upload
    if not text.startswith("/"):
        with _lock:
            state = _user_states.get(str(chat_id))
            if state in ("awaiting_search_query", "awaiting_pagos_search") or (
                isinstance(state, str) and state.startswith("awaiting_comp_monto:")
            ):
                _user_states.pop(str(chat_id), None)
        if state == "awaiting_search_query" and text:
            _handle_search_flow(chat_id, text, table or _table_ref)
            return
        if state == "awaiting_pagos_search" and text:
            _handle_pagos_search(chat_id, text, table or _table_ref)
            return
        if isinstance(state, str) and state.startswith("awaiting_comp_monto:") and text:
            rest = state[len("awaiting_comp_monto:"):]
            inv_id, idx_str = rest.rsplit(":", 1) if ":" in rest else (rest, "-1")
            _apply_comprobante_monto(chat_id, inv_id, idx_str, text, table or _table_ref)
            return
        if isinstance(state, str) and state.startswith("awaiting_comprobante:"):
            inv_id = state[len("awaiting_comprobante:"):]
            _handle_telegram_comprobante_upload(chat_id, inv_id, msg, table or _table_ref)
            return

    if not text:
        return

    # Clear any pending state when a command is issued
    if text.startswith("/"):
        with _lock:
            _user_states.pop(str(chat_id), None)

    # 3. /status command
    if raw_cmd == "/status":
        stats_text = _get_wedding_stats(table or _table_ref)
        send_message(chat_id, stats_text)
        return

    # 4. /send command
    if raw_cmd == "/send" or raw_cmd.startswith("/send_"):
        if raw_cmd.startswith("/send_") and not arg:
            arg = raw_cmd[len("/send_"):].replace("_", " ")
        _handle_send_command(chat_id, arg, table or _table_ref)
        return

    # 4b. /buscar or /search shortcut
    if raw_cmd in ("/buscar", "/search"):
        if arg:
            _handle_search_flow(chat_id, arg, table or _table_ref)
        else:
            _prompt_search_query(chat_id)
        return

    # 5. /sintelefono command
    if raw_cmd in ("/sintelefono", "/nophone", "/sin_telefono"):
        _handle_sintelefono_command(chat_id, arg, table or _table_ref)
        return

    # 5b. /pagos command
    if raw_cmd == "/pagos":
        _handle_pagos_menu(chat_id, table or _table_ref)
        return

    # 6. /stop command
    if raw_cmd == "/stop":
        remove_subscriber(chat_id)
        send_message(chat_id, "🔕 <b>Notificaciones desactivadas.</b> Para volver a activarlas, usá tu enlace de autorización.")
        return

    # 6b. /cancelar or /cancel
    if raw_cmd in ("/cancel", "/cancelar"):
        _send_main_menu(chat_id, user_name)
        return

    # 7. Fallback clickable menu for any other text or /menu /help
    _send_main_menu(chat_id, user_name)


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
