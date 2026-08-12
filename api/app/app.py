from flask import Flask, request, jsonify, render_template
from sheets import GoogleSheetsTable

app = Flask(__name__)
table = GoogleSheetsTable()


@app.get("/")
def index_page():
    try:
        invitados = table.get_all()
        count = len(invitados)
        confirmados = sum(1 for g in invitados if g.get("confirmacion") == "si")
        rechazados = sum(1 for g in invitados if g.get("confirmacion") == "no")
        return render_template("index.html", invitados=invitados, count=count, confirmados=confirmados, rechazados=rechazados)
    except Exception as e:
        return render_template("index.html", invitados=[], count=0, confirmados=0, rechazados=0)


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
    app.run(host="0.0.0.0", port=5000)
