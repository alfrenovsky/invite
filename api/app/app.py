import os
import time
from flask import Flask, request, jsonify, render_template
from sheets import GoogleSheetsTable, parse_and_validate_token


app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
table = GoogleSheetsTable()


@app.after_request
def add_cache_headers(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response




SLIDES_CONFIG = [
    {
        "id": "portada",
        "title": "Portada",
        "template": "slides/00_portada.html",
        "duration": 6000,
        "enabled": True,
    },
    {
        "id": "video",
        "title": "Video",
        "template": "slides/01_video.html",
        "duration": 10000,
        "enabled": True,
    },
    {
        "id": "intro",
        "title": "Bienvenida",
        "template": "slides/01_intro.html",
        "duration": 7000,
        "enabled": True,
    },


    {
        "id": "fecha_lugar",
        "title": "Cuándo",
        "template": "slides/02_fecha_lugar.html",
        "duration": 7000,
        "enabled": True,
    },
    {
        "id": "lugar",
        "title": "Dónde",
        "template": "slides/03_lugar.html",
        "duration": 6500,
        "enabled": True,
    },
    {
        "id": "itinerario",
        "title": "Itinerario",
        "template": "slides/04_itinerario.html",
        "duration": 7000,
        "enabled": True,
    },
    {
        "id": "regalos",
        "title": "Regalos",
        "template": "slides/05_regalos.html",
        "duration": 7000,
        "enabled": True,
    },
    {
        "id": "rsvp",
        "title": "Confirmación",
        "template": "slides/06_rsvp.html",
        "duration": 0,
        "enabled": True,
    },
    {
        "id": "triste",
        "title": "¡Qué pena!",
        "template": "slides/07_triste.html",
        "duration": 0,
        "enabled": True,
    },
]


def get_guest_context(validated_slug):
    try:
        guests = table.get_by_invitacion(validated_slug)
    except Exception:
        guests = []

    if not guests:
        return None

    names = [g.get("nombre", "").strip() for g in guests if g.get("nombre")]
    if len(names) == 1:
        group_names = names[0]
    elif len(names) == 2:
        group_names = f"{names[0]} y {names[1]}"
    elif len(names) > 2:
        group_names = f"{', '.join(names[:-1])} y {names[-1]}"
    else:
        group_names = "Familia / Pareja"

    confirmed_count = sum(1 for g in guests if g.get("confirmacion") == "si")
    rejected_count = sum(1 for g in guests if g.get("confirmacion") == "no")
    pending_count = sum(1 for g in guests if not g.get("confirmacion") or g.get("confirmacion") not in ("si", "no"))
    all_confirmed = (confirmed_count == len(guests) and len(guests) > 0)
    all_rejected = (rejected_count == len(guests) and len(guests) > 0)
    any_confirmed = (confirmed_count > 0)
    invitation_url = ""
    for g in guests:
        if g.get("url"):
            invitation_url = g["url"]
            break

    active_slides = [s for s in SLIDES_CONFIG if s.get("enabled", True)]



    return {
        "invitacion_id": validated_slug,
        "invitation_url": invitation_url,
        "guests": guests,
        "group_names": group_names,
        "confirmed_count": confirmed_count,
        "rejected_count": rejected_count,
        "pending_count": pending_count,
        "all_confirmed": all_confirmed,
        "all_rejected": all_rejected,
        "any_confirmed": any_confirmed,
        "active_slides": active_slides,
        "config_version": int(time.time()),
    }





@app.get("/")
@app.get("/i/<token>")
@app.get("/invitacion/<token>")
def index_page(token=None):
    if not token:
        token = request.args.get("invitacion_id") or request.args.get("invitacion") or request.args.get("id") or request.args.get("token")

    if not token:
        return render_template("blank.html")

    validated_slug = parse_and_validate_token(token)
    if not validated_slug:
        return render_template("blank.html")

    user_agent = request.headers.get("User-Agent", "").lower()
    is_crawler = any(bot in user_agent for bot in ("whatsapp", "facebookexternalhit", "facebot", "twitterbot", "telegrambot"))
    if is_crawler:
        base_url = os.environ.get("BASE_URL", "https://nos.vamos.acas.ar")
        full_invite_url = f"{base_url}/i/{token}"

        # Read strictly from local JSON cache (0 Google Sheets calls for crawlers)
        try:
            cache = table._load_cache()
            if cache and cache.get("records"):
                target = str(validated_slug).strip().lower().replace(" ", "_").replace("-", "_")
                for r in cache["records"]:
                    inv_slug = str(r.get("invitacion_id") or r.get("invitacion") or r.get("id") or "").strip().lower().replace(" ", "_").replace("-", "_")
                    if inv_slug == target and r.get("url"):
                        full_invite_url = r["url"]
                        break
        except Exception:
            pass

        return render_template(
            "og_preview.html",
            invitation_url=full_invite_url,
            base_url=base_url
        )


    context = get_guest_context(validated_slug)
    if not context:
        return render_template("blank.html")

    return render_template(
        "index.html",
        token=token,
        **context
    )


@app.get("/i/<token>/slides")
@app.get("/invitacion/<token>/slides")
def get_slides_manifest(token):
    validated_slug = parse_and_validate_token(token)
    if not validated_slug:
        return jsonify({"ok": False, "error": "Token inválido"}), 403

    context = get_guest_context(validated_slug)
    if not context:
        return jsonify({"ok": False, "error": "Invitación no encontrada"}), 404

    active_slides = []
    order = 1
    for s in SLIDES_CONFIG:
        if s.get("enabled", True):
            active_slides.append({
                "id": s["id"],
                "title": s.get("title", s["id"]),
                "order": order,
                "duration": s.get("duration", 7000),
                "url": f"/i/{token}/slide/{s['id']}"
            })
            order += 1

    return jsonify({
        "ok": True,
        "invitacion_id": validated_slug,
        "slides": active_slides,
        "count": len(active_slides)
    })


@app.get("/i/<token>/slide/<slide_id>")
@app.get("/invitacion/<token>/slide/<slide_id>")
def get_single_slide(token, slide_id):
    validated_slug = parse_and_validate_token(token)
    if not validated_slug:
        return render_template("blank.html"), 403

    context = get_guest_context(validated_slug)
    if not context:
        return render_template("blank.html"), 404

    slide = next((s for s in SLIDES_CONFIG if s["id"] == slide_id and s.get("enabled", True)), None)
    if not slide:
        return "Slide no encontrada o deshabilitada", 404

    return render_template(slide["template"], **context)







@app.get("/form")
def form_page():
    guest_id = request.args.get("id")
    guest = None
    if guest_id:
        res = table.get_by_id(guest_id)
        if res:
            guest = res["data"]
    return render_template("form.html", guest=guest)


from functools import wraps

API_KEY = os.environ.get("API_KEY", "boda_secret_api_key_2027")


def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Check Header 'X-API-Key'
        key = request.headers.get("X-API-Key")
        # 2. Check Header 'Authorization: Bearer <key>' or 'Authorization: <key>'
        if not key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                key = auth_header[7:].strip()
            elif auth_header:
                key = auth_header.strip()
        # 3. Check query parameter '?api_key=<key>' or '?key=<key>'
        if not key:
            key = request.args.get("api_key") or request.args.get("key")

        if not key or key != API_KEY:
            return jsonify({"ok": False, "error": "No autorizado: API Key inválida o faltante"}), 401
        return f(*args, **kwargs)

    return decorated_function


@app.get("/invitados-view")
@require_api_key
def invitados_view_page():
    try:
        invitados = table.get_all()
        return render_template("invitados.html", invitados=invitados, count=len(invitados))
    except Exception as e:
        return render_template("invitados.html", invitados=[], count=0)



@app.get("/invitados")
@require_api_key
def list_invitados():
    try:
        invitados = table.get_all()
        return jsonify({"ok": True, "data": invitados, "count": len(invitados)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/invitados/ensure-ids")
@require_api_key
def ensure_invitados_ids():
    try:
        records, updated_count = table.ensure_ids()
        return jsonify({"ok": True, "data": records, "updated_count": updated_count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/invitados/<record_id>")
def get_invitado(record_id):
    try:
        res = table.get_by_id(record_id)
        if not res:
            return jsonify({"ok": False, "error": "Invitado no encontrado"}), 404
        return jsonify({"ok": True, "data": res["data"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/invitados")
@require_api_key
def create_invitado():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"ok": False, "error": "JSON inválido"}), 400

    try:
        created = table.add_records([data] if isinstance(data, dict) else data)
        return jsonify({"ok": True, "data": created if isinstance(data, list) else created[0]}), 201
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.put("/invitados/<record_id>")
@app.patch("/invitados/<record_id>")
def update_invitado(record_id):
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"ok": False, "error": "JSON inválido"}), 400

    try:
        updated = table.update_record(record_id, data)
        if not updated:
            return jsonify({"ok": False, "error": "Invitado no encontrado"}), 404
        return jsonify({"ok": True, "data": updated})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.delete("/invitados/<record_id>")
@require_api_key
def delete_invitado(record_id):
    try:
        success = table.delete_record(record_id)
        if not success:
            return jsonify({"ok": False, "error": "Invitado no encontrado"}), 404
        return jsonify({"ok": True, "message": "Invitado eliminado correctamente"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500



@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=5000, debug=debug_mode)
