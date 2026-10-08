import os
import time
import json
import re
from flask import Flask, request, jsonify, render_template, abort
from werkzeug.utils import secure_filename
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


SLIDES_JSON_PATH = os.path.join(os.path.dirname(__file__), "slides.json")

SLIDES_CONFIG_DEFAULT = [
    {
        "id": "portada",
        "title": "Portada",
        "template": "slides/portada.html",
        "duration": 6000,
        "enabled": True,
        "background": "photo01.jpeg",
    },
    {
        "id": "lugar",
        "title": "Lugar",
        "template": "slides/lugar.html",
        "duration": 7000,
        "enabled": True,
        "background": "fiesta.jpeg",
    },
    {
        "id": "video",
        "title": "Video",
        "template": "slides/video.html",
        "duration": 10000,
        "enabled": True,
        "background": "",
    },
    {
        "id": "itinerario",
        "title": "Itinerario",
        "template": "slides/itinerario.html",
        "duration": 7000,
        "enabled": True,
        "background": "background.jpeg",
    },
    {
        "id": "regalos",
        "title": "Regalos",
        "template": "slides/regalos.html",
        "duration": 7000,
        "enabled": True,
        "background": "background.1.jpeg",
    },
    {
        "id": "rsvp",
        "title": "Confirmación",
        "template": "slides/rsvp.html",
        "duration": 0,
        "enabled": True,
        "background": "background.alternate.jpeg",
    },
    {
        "id": "triste",
        "title": "¡Qué pena!",
        "template": "slides/triste.html",
        "duration": 0,
        "enabled": True,
        "background": "GatoTriste.jpeg",
    },
]


def load_slides():
    if os.path.exists(SLIDES_JSON_PATH):
        try:
            with open(SLIDES_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return SLIDES_CONFIG_DEFAULT


def save_slides(slides_list):
    with open(SLIDES_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(slides_list, f, indent=2, ensure_ascii=False)


def get_backgrounds_dir():
    candidates = [
        "/app/backgrounds",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../html/assets/backgrounds")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "backgrounds")),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    target = candidates[0]
    os.makedirs(target, exist_ok=True)
    return target


VALID_MEDIA_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".avif",
    ".mp4", ".webm", ".mov", ".m4v"
}


def get_backgrounds_list():
    bg_dir = get_backgrounds_dir()
    if not os.path.exists(bg_dir):
        return []
    files = []
    try:
        for fname in sorted(os.listdir(bg_dir)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in VALID_MEDIA_EXTS and not fname.startswith("."):
                files.append(fname)
    except Exception:
        pass
    return files


def is_dev_mode():
    return bool(
        app.debug
        or os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
        or os.environ.get("ENV", "").lower() in ("dev", "development")
    )


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

    active_slides = [s for s in load_slides() if s.get("enabled", True)]



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
    for s in load_slides():
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

    slide = next((s for s in load_slides() if s["id"] == slide_id and s.get("enabled", True)), None)
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


# ---------------------------------------------------------
# DEVELOPMENT PANEL (Unauthenticated, Dev Mode Only)
# ---------------------------------------------------------

@app.get("/panel")
def panel_view():
    if not is_dev_mode():
        abort(404)
    slides = load_slides()
    backgrounds = get_backgrounds_list()
    return render_template("panel.html", slides=slides, backgrounds=backgrounds)


@app.post("/panel/upload-background")
def panel_upload_background():
    if not is_dev_mode():
        abort(404)

    if "background_file" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo"}), 400

    file = request.files["background_file"]
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "Archivo no seleccionado"}), 400

    _, orig_ext = os.path.splitext(file.filename)
    orig_ext = orig_ext.lower()
    if orig_ext not in VALID_MEDIA_EXTS:
        return jsonify({"ok": False, "error": f"Formato no permitido: {orig_ext}. Usa formatos de imagen o video (JPG, PNG, WEBP, MP4, WEBM, MOV)."}), 400

    custom_name = request.form.get("background_name", "").strip()
    if custom_name:
        base_name, user_ext = os.path.splitext(custom_name)
        ext = user_ext.lower() if user_ext else orig_ext
        clean_base = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", base_name)
        filename = f"{clean_base}{ext}"
    else:
        filename = secure_filename(file.filename)
        if not filename:
            filename = f"bg_{int(time.time())}{orig_ext}"

    bg_dir = get_backgrounds_dir()
    os.makedirs(bg_dir, exist_ok=True)
    save_path = os.path.join(bg_dir, filename)
    file.save(save_path)

    return jsonify({"ok": True, "filename": filename, "message": "Fondo subido correctamente"})


@app.post("/panel/slide/<slide_id>")
def panel_update_slide(slide_id):
    if not is_dev_mode():
        abort(404)

    slides = load_slides()
    target_slide = next((s for s in slides if s["id"] == slide_id), None)
    if not target_slide:
        return jsonify({"ok": False, "error": f"Slide {slide_id} no encontrado"}), 404

    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    if "background" in data:
        target_slide["background"] = data["background"].strip()

    if "duration" in data:
        try:
            target_slide["duration"] = int(data["duration"])
        except (ValueError, TypeError):
            pass

    for key, val in data.items():
        if key not in ("slide_id", "background", "duration"):
            target_slide[key] = val

    save_slides(slides)
    return jsonify({"ok": True, "slide": target_slide, "message": "Slide actualizado"})


@app.post("/panel/slides")
def panel_save_all_slides():
    if not is_dev_mode():
        abort(404)

    data = request.get_json(silent=True)
    if not data or not isinstance(data, list):
        return jsonify({"ok": False, "error": "Se esperaba una lista de slides"}), 400

    save_slides(data)
    return jsonify({"ok": True, "slides": data, "message": "Todos los slides guardados"})


@app.post("/panel/rename-background")
def panel_rename_background():
    if not is_dev_mode():
        abort(404)

    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    old_name = data.get("old_name", "").strip()
    new_name = data.get("new_name", "").strip()

    if not old_name or not new_name:
        return jsonify({"ok": False, "error": "Se requieren el nombre actual y el nuevo nombre"}), 400

    bg_dir = get_backgrounds_dir()
    old_path = os.path.join(bg_dir, old_name)
    if not os.path.isfile(old_path):
        return jsonify({"ok": False, "error": f"El archivo '{old_name}' no existe"}), 404

    base_name, user_ext = os.path.splitext(new_name)
    _, orig_ext = os.path.splitext(old_name)
    ext = user_ext.lower() if user_ext else orig_ext.lower()

    if ext not in VALID_MEDIA_EXTS:
        return jsonify({"ok": False, "error": f"Extensión no permitida: {ext}."}), 400

    clean_base = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", base_name)
    sanitized_new_name = f"{clean_base}{ext}"

    new_path = os.path.join(bg_dir, sanitized_new_name)
    if sanitized_new_name != old_name and os.path.exists(new_path):
        return jsonify({"ok": False, "error": f"Ya existe un archivo con el nombre '{sanitized_new_name}'"}), 400

    if sanitized_new_name != old_name:
        os.rename(old_path, new_path)

        # Update slides.json if any slide was using old_name
        slides = load_slides()
        modified = False
        for slide in slides:
            if slide.get("background") == old_name:
                slide["background"] = sanitized_new_name
                modified = True
        if modified:
            save_slides(slides)

    return jsonify({
        "ok": True,
        "old_name": old_name,
        "new_name": sanitized_new_name,
        "message": f"Fondo renombrado a '{sanitized_new_name}'"
    })


@app.post("/panel/delete-background")
def panel_delete_background():
    if not is_dev_mode():
        abort(404)

    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    filename = data.get("filename", "").strip()
    if not filename:
        return jsonify({"ok": False, "error": "Nombre de archivo no especificado"}), 400

    # Security check: avoid directory traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"ok": False, "error": "Nombre de archivo inválido"}), 400

    bg_dir = get_backgrounds_dir()
    file_path = os.path.join(bg_dir, filename)
    if not os.path.isfile(file_path):
        return jsonify({"ok": False, "error": f"El archivo '{filename}' no existe"}), 404

    try:
        os.remove(file_path)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error al eliminar archivo: {str(e)}"}), 500

    # Clean up slides.json if any slide was using this background
    slides = load_slides()
    modified = False
    for slide in slides:
        if slide.get("background") == filename:
            slide["background"] = ""
            modified = True
    if modified:
        save_slides(slides)

    return jsonify({
        "ok": True,
        "filename": filename,
        "message": f"Fondo '{filename}' eliminado correctamente"
    })




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
