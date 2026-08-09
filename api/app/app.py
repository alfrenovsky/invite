import os
import uuid
import threading
from datetime import datetime

import gspread
from flask import Flask, request, jsonify

app = Flask(__name__)

FIELDNAMES = ["id", "apellido", "nombre", "asistencia", "alimentacion", "acompanante", "fecha_hora"]

GOOGLE_CREDENTIALS_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH", "/secrets/credentials.json")
GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
WORKSHEET_NAME = os.environ.get("WORKSHEET_NAME", "Respuestas")

lock = threading.Lock()

_gc = gspread.service_account(filename=GOOGLE_CREDENTIALS_PATH)
_sh = _gc.open_by_key(GOOGLE_SHEET_ID)

try:
    _ws = _sh.worksheet(WORKSHEET_NAME)
except gspread.exceptions.WorksheetNotFound:
    _ws = _sh.add_worksheet(title=WORKSHEET_NAME, rows=1000, cols=len(FIELDNAMES))


def ensure_header():
    valores = _ws.row_values(1)
    if valores != FIELDNAMES:
        _ws.update("A1", [FIELDNAMES])


ensure_header()


def nueva_persona(apellido, nombre, asistencia, alimentacion, acompanante=""):
    return {
        "id": uuid.uuid4().hex[:8],
        "apellido": apellido,
        "nombre": nombre,
        "asistencia": asistencia,
        "alimentacion": "|".join(alimentacion or []),
        "acompanante": acompanante,
        "fecha_hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.post("/submit")
def submit():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON inválido"}), 400

    asistencia = data.get("asistencia")
    principal = data.get("principal") or {}
    acompanantes = data.get("acompanantes") or []

    if asistencia not in ("si", "no"):
        return jsonify({"error": "asistencia debe ser 'si' o 'no'"}), 400
    if not principal.get("apellido") or not principal.get("nombre"):
        return jsonify({"error": "faltan apellido/nombre del invitado principal"}), 400

    filas = []
    principal_row = nueva_persona(
        principal.get("apellido"),
        principal.get("nombre"),
        asistencia,
        principal.get("alimentacion"),
    )
    filas.append(principal_row)

    if asistencia == "si":
        for a in acompanantes:
            if not a.get("apellido") or not a.get("nombre"):
                continue
            filas.append(
                nueva_persona(
                    a.get("apellido"),
                    a.get("nombre"),
                    "si",
                    a.get("alimentacion"),
                    acompanante=principal_row["id"],
                )
            )

    filas_valores = [[fila[c] for c in FIELDNAMES] for fila in filas]

    with lock:
        _ws.append_rows(filas_valores, value_input_option="USER_ENTERED")

    return jsonify({"ok": True, "id": principal_row["id"], "total_personas": len(filas)})


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
