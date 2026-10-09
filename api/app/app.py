import os
import time
import json
import re
import urllib.parse
from flask import Flask, request, jsonify, render_template, abort, send_file
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


def get_data_dir():
    candidates = [
        "/data",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "data")),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    target = candidates[0]
    os.makedirs(target, exist_ok=True)
    return target


DATA_DIR = get_data_dir()
SLIDES_JSON_PATH = os.path.join(DATA_DIR, "slides.json")
STYLES_JSON_PATH = os.path.join(DATA_DIR, "styles.json")

SLIDES_CONFIG_DEFAULT = [
    {
        "id": "portada",
        "title": "Portada",
        "template": "slides/custom.html",
        "duration": 8500,
        "enabled": True,
        "background": "AyCenMaal.jpeg",
        "elements": [
            {
                "id": "el_1791458669060",
                "type": "text",
                "content": "NOS CASAMOS",
                "style": "body",
                "x": -1,
                "y": 32,
            },
            {
                "id": "el_1791458721214",
                "type": "text",
                "content": "Celia & Alfredo",
                "style": "title",
                "x": 0,
                "y": 39,
            },
        ],
    },
    {
        "id": "lugar",
        "title": "Lugar",
        "template": "slides/custom.html",
        "duration": 7000,
        "enabled": True,
        "background": "fiesta.jpeg",
    },
    {
        "id": "video",
        "title": "Video",
        "template": "slides/custom.html",
        "duration": 10000,
        "enabled": True,
        "background": "",
    },
    {
        "id": "itinerario",
        "title": "Itinerario",
        "template": "slides/custom.html",
        "duration": 7000,
        "enabled": True,
        "background": "background.jpeg",
    },
    {
        "id": "regalos",
        "title": "Regalos",
        "template": "slides/custom.html",
        "duration": 7000,
        "enabled": True,
        "background": "background.1.jpeg",
    },
    {
        "id": "rsvp",
        "title": "Confirmación",
        "template": "slides/custom.html",
        "duration": 0,
        "enabled": True,
        "background": "background.alternate.jpeg",
        "elements": [
            {
                "id": "el_default_form",
                "type": "form",
                "x": 0,
                "y": 0,
                "w": 92,
                "h": 70,
            }
        ],
    },
    {
        "id": "triste",
        "title": "¡Qué pena!",
        "template": "slides/custom.html",
        "duration": 0,
        "enabled": True,
        "background": "GatoTriste.jpeg",
    },
]


def load_slides():
    slides = []
    if os.path.exists(SLIDES_JSON_PATH):
        try:
            with open(SLIDES_JSON_PATH, "r", encoding="utf-8") as f:
                slides = json.load(f)
        except Exception:
            slides = []
    if not slides:
        slides = [dict(s) for s in SLIDES_CONFIG_DEFAULT]

    for s in slides:
        if "elements" not in s or not isinstance(s["elements"], list):
            s["elements"] = []
        s["template"] = "slides/custom.html"

    return slides


def save_slides(slides_list):
    for s in slides_list:
        if "elements" not in s or not isinstance(s["elements"], list):
            s["elements"] = []
        s["template"] = "slides/custom.html"

    with open(SLIDES_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(slides_list, f, indent=2, ensure_ascii=False)


DEFAULT_STYLES = {
    "title": {
        "fontFamily": "Montserrat",
        "color": "#ffffff",
        "fontSize": 38,
        "fontWeight": 800,
        "letterSpacing": -0.06,
        "lineHeight": 0.9,
        "textTransform": "uppercase",
    },
    "tag": {
        "fontFamily": "Montserrat",
        "color": "#d4af37",
        "fontSize": 14,
        "fontWeight": 600,
        "letterSpacing": 0.15,
        "lineHeight": 1.2,
        "textTransform": "uppercase",
    },
    "body": {
        "fontFamily": "Montserrat",
        "color": "#f0f4f8",
        "fontSize": 18,
        "fontWeight": 500,
        "letterSpacing": 0.0,
        "lineHeight": 1.4,
        "textTransform": "none",
    },
    "header_title": {
        "text": "CELIA & ALFREDO",
        "fontFamily": "Montserrat",
        "color": "#ffffff",
        "fontSize": 14,
        "fontWeight": 700,
        "letterSpacing": 0.05,
    },
    "header_subtitle": {
        "text": "NOS CASAMOS - 19 de Marzo",
        "fontFamily": "Montserrat",
        "color": "#a0aec0",
        "fontSize": 11,
        "fontWeight": 600,
        "letterSpacing": 0.05,
    },
}


def load_styles():
    if os.path.exists(STYLES_JSON_PATH):
        try:
            with open(STYLES_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                styles = {k: dict(v) for k, v in DEFAULT_STYLES.items()}
                for k, v in data.items():
                    if k in styles and isinstance(v, dict):
                        styles[k].update(v)
                return styles
        except Exception:
            pass
    return {k: dict(v) for k, v in DEFAULT_STYLES.items()}


def save_styles(styles_dict):
    with open(STYLES_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(styles_dict, f, indent=2, ensure_ascii=False)


SYSTEM_REQUIRED_FONTS = {"aveny-t", "aveny-t-medium", "aveny t"}


def is_font_in_use(family, styles=None, slides=None):
    if not family:
        return False
    fam_norm = family.strip().lower()
    if fam_norm in SYSTEM_REQUIRED_FONTS:
        return True
    if styles is None:
        styles = load_styles()
    for s_val in styles.values():
        if isinstance(s_val, dict):
            ff = (s_val.get("fontFamily") or "").strip().lower()
            if ff == fam_norm:
                return True
    if slides is None:
        slides = load_slides()
    for slide in slides:
        for el in slide.get("elements", []):
            if isinstance(el, dict):
                el_ff = (el.get("fontFamily") or el.get("font") or "").strip().lower()
                if el_ff == fam_norm:
                    return True
    return False


def get_backgrounds_dir():
    candidates = [
        os.path.join(get_data_dir(), "backgrounds"),
        "/data/backgrounds",
        "/app/backgrounds",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../html/assets/backgrounds")),
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


VALID_FONT_EXTS = {".ttf", ".otf", ".woff", ".woff2"}


def get_fonts_dir():
    candidates = [
        os.path.join(get_data_dir(), "fonts"),
        "/data/fonts",
        "/app/fonts",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../html/assets/fonts")),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    target = candidates[0]
    os.makedirs(target, exist_ok=True)
    return target


def get_fonts_list():
    fonts_dir = get_fonts_dir()
    if not os.path.exists(fonts_dir):
        return []
    fonts = []
    try:
        for fname in sorted(os.listdir(fonts_dir)):
            if fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in VALID_FONT_EXTS:
                full_path = os.path.join(fonts_dir, fname)
                size_bytes = os.path.getsize(full_path) if os.path.isfile(full_path) else 0
                size_kb = round(size_bytes / 1024, 1)
                family = os.path.splitext(fname)[0]
                fonts.append({
                    "filename": fname,
                    "family": family,
                    "ext": ext.replace(".", "").upper(),
                    "size_kb": size_kb,
                })
    except Exception:
        pass
    return fonts


def rebuild_fonts_css(fonts_dir=None):
    if not fonts_dir:
        fonts_dir = get_fonts_dir()
    if not os.path.exists(fonts_dir):
        return
    css_path = os.path.join(fonts_dir, "fonts.css")

    format_map = {
        ".ttf": "truetype",
        ".otf": "opentype",
        ".woff": "woff",
        ".woff2": "woff2",
    }
    special_aliases = {
        "Aveny-T.otf": "Aveny T",
        "FoundationTitlesHand_1.0.otf": "FoundationTitlesHand",
    }

    entries = []
    try:
        for fname in sorted(os.listdir(fonts_dir)):
            if fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in format_map:
                continue
            fmt = format_map[ext]
            base_family = os.path.splitext(fname)[0]
            encoded_fname = urllib.parse.quote(fname)

            entries.append(
                f"@font-face {{\n"
                f"    font-family: '{base_family}';\n"
                f"    src: url('/assets/fonts/{encoded_fname}') format('{fmt}');\n"
                f"    font-display: swap;\n"
                f"}}"
            )

            if fname in special_aliases:
                alias = special_aliases[fname]
                entries.append(
                    f"@font-face {{\n"
                    f"    font-family: '{alias}';\n"
                    f"    src: url('/assets/fonts/{encoded_fname}') format('{fmt}');\n"
                    f"    font-display: swap;\n"
                    f"}}"
                )

        header = "/* Auto-generated font-face definitions for wedding story fonts */\n\n"
        content = header + "\n\n".join(entries) + "\n"
        with open(css_path, "w", encoding="utf-8") as out:
            out.write(content)
    except Exception:
        pass


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
        "styles": load_styles(),
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

    return render_template(slide["template"], slide=slide, **context)







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

GOOGLE_FONTS_CATALOG = ["Montserrat", "Alegreya", "Archivo", "Rosario", "Saira"]


@app.get("/panel")
def panel_view():
    if not is_dev_mode():
        abort(404)
    slides = load_slides()
    backgrounds = get_backgrounds_list()
    styles = load_styles()
    local_fonts = get_fonts_list()
    local_families = {f["family"].lower() for f in local_fonts}

    google_fonts = []
    for gfont in GOOGLE_FONTS_CATALOG:
        if gfont.lower() not in local_families:
            google_fonts.append({
                "filename": "",
                "family": gfont,
                "ext": "GOOGLE",
                "size_kb": 0,
                "is_system": True,
                "in_use": is_font_in_use(gfont, styles, slides),
            })

    for f in local_fonts:
        f["in_use"] = is_font_in_use(f.get("family"), styles, slides)
        f["is_system"] = False

    all_fonts = google_fonts + local_fonts
    used_backgrounds = {s.get("background") for s in slides if s.get("background")}

    return render_template(
        "panel.html",
        slides=slides,
        backgrounds=backgrounds,
        styles=styles,
        fonts=all_fonts,
        used_backgrounds=used_backgrounds
    )


@app.post("/panel/styles")
def panel_save_styles():
    if not is_dev_mode():
        abort(404)
    data = request.get_json(silent=True) or request.form.to_dict()
    if not data or not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Datos inválidos"}), 400
    current = load_styles()
    for k, v in data.items():
        if isinstance(v, dict) and k in current and isinstance(current[k], dict):
            current[k].update(v)
        else:
            current[k] = v
    save_styles(current)
    return jsonify({"ok": True, "styles": current, "message": "Estilos guardados correctamente"})


@app.get("/panel/styles/download")
def panel_download_styles():
    if not is_dev_mode():
        abort(404)
    if not os.path.exists(STYLES_JSON_PATH):
        styles = load_styles()
        save_styles(styles)
    return send_file(
        STYLES_JSON_PATH,
        as_attachment=True,
        download_name="styles.json",
        mimetype="application/json",
    )


@app.post("/panel/styles/upload")
def panel_upload_styles():
    if not is_dev_mode():
        abort(404)
    uploaded = None
    if "file" in request.files:
        file = request.files["file"]
        if not file or not file.filename:
            return jsonify({"ok": False, "error": "No se seleccionó ningún archivo"}), 400
        try:
            content = file.read().decode("utf-8")
            uploaded = json.loads(content)
        except Exception as e:
            return jsonify({"ok": False, "error": f"JSON inválido: {str(e)}"}), 400
    elif request.is_json:
        uploaded = request.get_json(silent=True)

    if uploaded is None:
        return jsonify({"ok": False, "error": "No se recibieron datos"}), 400
    if not isinstance(uploaded, dict):
        return jsonify({"ok": False, "error": "El archivo debe contener un objeto JSON con los estilos."}), 400

    current = load_styles()
    for k, v in uploaded.items():
        if isinstance(v, dict) and k in current and isinstance(current[k], dict):
            current[k].update(v)
        else:
            current[k] = v
    save_styles(current)
    saved = load_styles()
    return jsonify({"ok": True, "styles": saved, "message": "styles.json importado correctamente"})


@app.get("/panel/slides/download")
def panel_download_slides():
    if not is_dev_mode():
        abort(404)
    if not os.path.exists(SLIDES_JSON_PATH):
        slides = load_slides()
        save_slides(slides)
    return send_file(
        SLIDES_JSON_PATH,
        as_attachment=True,
        download_name="slides.json",
        mimetype="application/json",
    )


@app.post("/panel/slides/upload")
def panel_upload_slides():
    if not is_dev_mode():
        abort(404)
    uploaded = None
    if "file" in request.files:
        file = request.files["file"]
        if not file or not file.filename:
            return jsonify({"ok": False, "error": "No se seleccionó ningún archivo"}), 400
        try:
            content = file.read().decode("utf-8")
            uploaded = json.loads(content)
        except Exception as e:
            return jsonify({"ok": False, "error": f"JSON inválido: {str(e)}"}), 400
    elif request.is_json:
        uploaded = request.get_json(silent=True)

    if uploaded is None:
        return jsonify({"ok": False, "error": "No se recibieron datos"}), 400
    if not isinstance(uploaded, list):
        return jsonify({"ok": False, "error": "El archivo debe contener una lista (array) de slides."}), 400

    for s in uploaded:
        if not isinstance(s, dict):
            return jsonify({"ok": False, "error": "Cada elemento debe ser un objeto slide."}), 400
        if "elements" not in s or not isinstance(s["elements"], list):
            s["elements"] = []

    save_slides(uploaded)
    saved = load_slides()
    return jsonify({"ok": True, "slides": saved, "message": "slides.json importado correctamente"})


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

    for num_key in ("bg_zoom", "bg_x", "bg_y"):
        if num_key in data:
            try:
                target_slide[num_key] = float(data[num_key])
            except (ValueError, TypeError):
                pass

    for key, val in data.items():
        if key not in ("slide_id", "background", "duration", "bg_zoom", "bg_x", "bg_y"):
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


@app.post("/panel/slides/new")
def panel_create_slide():
    if not is_dev_mode():
        abort(404)

    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    title = (data.get("title") or "").strip()
    raw_id = (data.get("id") or "").strip()
    if not raw_id:
        slug = re.sub(r"[^a-zA-Z0-9_]", "_", title.lower()) if title else f"slide_{int(time.time())}"
        raw_id = slug or f"slide_{int(time.time())}"

    slides = load_slides()
    slide_id = raw_id
    counter = 1
    while any(s["id"] == slide_id for s in slides):
        slide_id = f"{raw_id}_{counter}"
        counter += 1

    try:
        duration = int(data.get("duration", 7000))
    except (ValueError, TypeError):
        duration = 7000

    def _safe_float(v, default):
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    new_slide = {
        "id": slide_id,
        "title": title or slide_id,
        "template": "slides/custom.html",
        "duration": duration,
        "enabled": True,
        "background": data.get("background", ""),
        "bg_zoom": _safe_float(data.get("bg_zoom"), 100.0),
        "bg_x": _safe_float(data.get("bg_x"), 0.0),
        "bg_y": _safe_float(data.get("bg_y"), 0.0),
        "elements": data.get("elements", []) if isinstance(data.get("elements"), list) else [],
    }
    slides.append(new_slide)
    save_slides(slides)
    return jsonify({"ok": True, "slide": new_slide, "message": f"Slide '{slide_id}' creado correctamente"})


@app.post("/panel/slide/<slide_id>/delete")
def panel_delete_slide(slide_id):
    if not is_dev_mode():
        abort(404)

    slides = load_slides()
    filtered = [s for s in slides if s["id"] != slide_id]
    if len(filtered) == len(slides):
        return jsonify({"ok": False, "error": f"Slide '{slide_id}' no encontrado"}), 404

    save_slides(filtered)
    return jsonify({"ok": True, "slide_id": slide_id, "message": f"Slide '{slide_id}' eliminado"})


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

    slides = load_slides()
    used_slides = [s.get("title") or s.get("id") for s in slides if s.get("background") == filename]
    if used_slides:
        return jsonify({
            "ok": False,
            "error": f"No se puede eliminar '{filename}' porque está en uso en el slide '{used_slides[0]}'"
        }), 400

    bg_dir = get_backgrounds_dir()
    file_path = os.path.join(bg_dir, filename)
    if not os.path.isfile(file_path):
        return jsonify({"ok": False, "error": f"El archivo '{filename}' no existe"}), 404

    try:
        os.remove(file_path)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error al eliminar archivo: {str(e)}"}), 500

    return jsonify({
        "ok": True,
        "filename": filename,
        "message": f"Fondo '{filename}' eliminado correctamente"
    })


@app.post("/panel/upload-font")
def panel_upload_font():
    if not is_dev_mode():
        abort(404)

    if "font_file" not in request.files:
        return jsonify({"ok": False, "error": "No se envió ningún archivo"}), 400

    file = request.files["font_file"]
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "Archivo no seleccionado"}), 400

    orig_name, orig_ext = os.path.splitext(file.filename)
    orig_ext = orig_ext.lower()
    if orig_ext not in VALID_FONT_EXTS:
        return jsonify({"ok": False, "error": f"Formato no permitido: {orig_ext}. Formatos aceptados: TTF, OTF, WOFF, WOFF2."}), 400

    custom_name = request.form.get("font_name", "").strip()
    if custom_name:
        clean_base = re.sub(r"[^a-zA-Z0-9_\-\s]", "_", custom_name).strip()
        filename = f"{clean_base}{orig_ext}"
    else:
        clean_base = re.sub(r"[^a-zA-Z0-9_\-\s]", "_", orig_name).strip()
        filename = f"{clean_base}{orig_ext}"

    if not filename:
        filename = f"font_{int(time.time())}{orig_ext}"

    fonts_dir = get_fonts_dir()
    os.makedirs(fonts_dir, exist_ok=True)
    save_path = os.path.join(fonts_dir, filename)
    file.save(save_path)

    rebuild_fonts_css(fonts_dir)
    updated_fonts = get_fonts_list()

    return jsonify({
        "ok": True,
        "filename": filename,
        "family": os.path.splitext(filename)[0],
        "fonts": updated_fonts,
        "message": f"Tipografía '{filename}' subida correctamente"
    })


@app.post("/panel/delete-font")
def panel_delete_font():
    if not is_dev_mode():
        abort(404)

    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    filename = (data.get("filename") or "").strip()
    if not filename:
        return jsonify({"ok": False, "error": "Nombre de archivo no especificado"}), 400

    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"ok": False, "error": "Nombre de archivo inválido"}), 400

    fonts_dir = get_fonts_dir()
    target_path = os.path.join(fonts_dir, filename)
    if not os.path.isfile(target_path):
        return jsonify({"ok": False, "error": f"La fuente '{filename}' no existe"}), 404

    family = os.path.splitext(filename)[0]
    fam_norm = family.strip().lower()
    file_norm = filename.strip().lower()
    if fam_norm in SYSTEM_REQUIRED_FONTS or file_norm.startswith("aveny-t") or is_font_in_use(family):
        return jsonify({"ok": False, "error": f"La fuente '{family}' está en uso en los stickers o estilos y no puede eliminarse"}), 400

    try:
        os.remove(target_path)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Error al eliminar la fuente: {str(e)}"}), 500

    rebuild_fonts_css(fonts_dir)
    updated_fonts = get_fonts_list()

    return jsonify({
        "ok": True,
        "filename": filename,
        "fonts": updated_fonts,
        "message": f"Fuente '{filename}' eliminada correctamente"
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
