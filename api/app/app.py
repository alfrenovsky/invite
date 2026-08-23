import os
from flask import Flask, request, jsonify, render_template
from sheets import GoogleSheetsTable

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




@app.get("/")
@app.get("/i/<invitacion_id>")
@app.get("/invitacion/<invitacion_id>")
def index_page(invitacion_id=None):
    if not invitacion_id:
        invitacion_id = request.args.get("invitacion_id") or request.args.get("invitacion") or request.args.get("id")

    if not invitacion_id:
        return render_template("blank.html")

    try:
        guests = table.get_by_invitacion(invitacion_id)
    except Exception:
        guests = []

    if not guests:
        return render_template("blank.html")

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
    any_confirmed = (confirmed_count > 0)

    return render_template(
        "index.html",
        invitacion_id=invitacion_id,
        guests=guests,
        group_names=group_names,
        confirmed_count=confirmed_count,
        rejected_count=rejected_count,
        pending_count=pending_count,
        all_confirmed=all_confirmed,
        any_confirmed=any_confirmed,
    )




@app.get("/form")
def form_page():
    guest_id = request.args.get("id")
    guest = None
    if guest_id:
        res = table.get_by_id(guest_id)
        if res:
            guest = res["data"]
    return render_template("form.html", guest=guest)


@app.get("/invitados-view")
def invitados_view_page():
    try:
        invitados = table.get_all()
        return render_template("invitados.html", invitados=invitados, count=len(invitados))
    except Exception as e:
        return render_template("invitados.html", invitados=[], count=0)



@app.get("/invitados")
def list_invitados():
    try:
        invitados = table.get_all()
        return jsonify({"ok": True, "data": invitados, "count": len(invitados)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/invitados/ensure-ids")
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
