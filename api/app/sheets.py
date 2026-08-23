import os
import uuid
import hashlib
import re
import threading
from datetime import datetime
import gspread
from gspread.utils import rowcol_to_a1

FIELDNAMES = [
    "id",
    "updated_at",
    "apellido",
    "nombre",
    "telefono",
    "invitacion_id",
    "cenaobaile",
    "invitaoreserva",
    "precio",
    "pago",
    "confirmacion",
    "pa_general",
    "pa_vegetariano",
    "pa_vegano",
    "pa_celiaco",
    "url",
]

INVITATION_SALT = os.environ.get("INVITATION_SALT", "boda_celia_y_alfredo_2027_secret_salt")
BASE_URL = os.environ.get("BASE_URL", "http://nos.vamos.acas.ar")


def compute_check_code(invitacion_id: str, salt: str = None) -> str:
    if not invitacion_id:
        return ""
    salt = salt or INVITATION_SALT
    normalized_id = str(invitacion_id).strip().lower().replace(" ", "_").replace("-", "_")
    raw = f"{normalized_id}:{salt}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:6]


def generate_invitation_url(invitacion_id: str, base_url: str = None, salt: str = None) -> str:
    if not invitacion_id:
        return ""
    base_url = (base_url or BASE_URL).rstrip("/")
    code = compute_check_code(invitacion_id, salt)
    normalized_id = str(invitacion_id).strip().lower().replace(" ", "_").replace("-", "_")
    return f"{base_url}/i/{normalized_id}_{code}"


def parse_and_validate_token(token: str, salt: str = None):
    if not token:
        return None
    token = str(token).strip()
    if "_" in token:
        parts = token.rsplit("_", 1)
        slug = parts[0]
        provided_code = parts[1]
        expected_code = compute_check_code(slug, salt)
        if provided_code.lower() == expected_code.lower():
            return slug
    return None


class GoogleSheetsTable:
    def __init__(self, credentials_path=None, sheet_id=None, worksheet_name=None):
        self.credentials_path = credentials_path or os.environ.get("GOOGLE_CREDENTIALS_PATH", "/secrets/credentials.json")
        self.sheet_id = sheet_id or os.environ.get("GOOGLE_SHEET_ID")
        self.worksheet_name = worksheet_name or os.environ.get("WORKSHEET_NAME", "Respuestas")
        self.lock = threading.RLock()
        self._ws = None

    def _get_worksheet(self):
        if self._ws is None:
            if not self.sheet_id:
                raise ValueError("GOOGLE_SHEET_ID is not configured.")
            gc = gspread.service_account(filename=self.credentials_path)
            sh = gc.open_by_key(self.sheet_id)
            try:
                self._ws = sh.worksheet(self.worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                self._ws = sh.add_worksheet(title=self.worksheet_name, rows=1000, cols=len(FIELDNAMES))
            self.ensure_header()
        return self._ws

    def ensure_header(self):
        ws = self._ws
        valores = ws.row_values(1)
        if valores != FIELDNAMES:
            ws.update("A1", [FIELDNAMES])

    def _generate_row_id(self, row_idx, timestamp_str=None):
        ts = timestamp_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return hashlib.md5(f"row_{row_idx}_{ts}".encode("utf-8")).hexdigest()[:8]

    def _extract_start_row(self, updated_range):
        if not isinstance(updated_range, str):
            return None
        match = re.search(r'A(\d+)', updated_range)
        if match:
            return int(match.group(1))
        return None

    def ensure_ids(self):
        with self.lock:
            ws = self._get_worksheet()
            records = ws.get_all_records()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updated_count = 0

            for idx, rec in enumerate(records, start=2):  # Header is row 1
                needs_id = not rec.get("id")
                needs_url = not rec.get("url") and bool(rec.get("invitacion_id") or rec.get("invitacion"))
                if needs_id or needs_url:
                    item, row = self._map_record_to_row(rec, now_str, row_idx=idx)
                    range_name = f"{rowcol_to_a1(idx, 1)}:{rowcol_to_a1(idx, len(FIELDNAMES))}"
                    ws.update(range_name, [row])
                    rec["id"] = item["id"]
                    rec["url"] = item["url"]
                    updated_count += 1

            return records, updated_count


    def get_all(self):
        with self.lock:
            records, _ = self.ensure_ids()
            return records

    def get_by_id(self, record_id):
        with self.lock:
            ws = self._get_worksheet()
            records = ws.get_all_records()
            for idx, rec in enumerate(records, start=2):  # Header is row 1
                if str(rec.get("id")) == str(record_id):
                    return {"row_index": idx, "data": rec}
            return None

    def get_by_invitacion(self, invitacion_id):
        if not invitacion_id:
            return []
        with self.lock:
            records = self.get_all()
            target = str(invitacion_id).strip().lower().replace(" ", "_").replace("-", "_")
            matched = [
                rec for rec in records
                if str(rec.get("invitacion_id") or rec.get("invitacion") or "").strip().lower().replace(" ", "_").replace("-", "_") == target
            ]
            if not matched:
                matched = [
                    rec for rec in records
                    if str(rec.get("id", "")).strip().lower() == target
                ]
            return matched

    def _map_record_to_row(self, rec, now_str, row_idx=None):
        alim = rec.get("alimentacion", [])
        if isinstance(alim, str):
            alim_list = [x.strip() for x in alim.split("|")]
        elif isinstance(alim, list):
            alim_list = [str(x) for x in alim]
        else:
            alim_list = []

        item = dict(rec)
        updated_at = str(rec.get("updated_at") or rec.get("fecha_hora") or now_str)

        if not item.get("id"):
            if row_idx is not None:
                item["id"] = self._generate_row_id(row_idx, updated_at)
            else:
                item["id"] = uuid.uuid4().hex[:8]
        else:
            item["id"] = str(item["id"])

        inv_val = str(rec.get("invitacion_id") or rec.get("invitacion") or "").strip().replace(" ", "_")
        item["updated_at"] = updated_at
        item["apellido"] = str(rec.get("apellido", ""))
        item["nombre"] = str(rec.get("nombre", ""))
        item["telefono"] = str(rec.get("telefono", ""))
        item["invitacion_id"] = inv_val
        item["invitacion"] = inv_val
        item["confirmacion"] = str(rec.get("confirmacion") or rec.get("asistencia", ""))



        # Dietary preference mapping
        item["pa_general"] = str(rec.get("pa_general", "si" if "general" in alim_list else ""))
        item["pa_vegetariano"] = str(rec.get("pa_vegetariano", "si" if "vegetariano" in alim_list else ""))
        item["pa_vegano"] = str(rec.get("pa_vegano", "si" if "vegano" in alim_list else ""))
        item["pa_celiaco"] = str(rec.get("pa_celiaco", "si" if "celiaco" in alim_list else ""))

        if not item.get("url") and inv_val:
            item["url"] = generate_invitation_url(inv_val)
        else:
            item["url"] = str(item.get("url", ""))

        # Build row according to FIELDNAMES

        row = []
        for col in FIELDNAMES:
            row.append(str(item.get(col, "")))

        return item, row

    def add_records(self, records):
        """
        records: list of dicts
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        processed_records = []
        rows_to_insert = []
        needs_id_update = False

        for rec in records:
            item, row = self._map_record_to_row(rec, now_str)
            if not rec.get("id"):
                needs_id_update = True
            processed_records.append(item)
            rows_to_insert.append(row)

        with self.lock:
            ws = self._get_worksheet()
            res = ws.append_rows(rows_to_insert, value_input_option="USER_ENTERED")

            if needs_id_update:
                updated_range = ""
                if isinstance(res, dict):
                    updated_range = res.get("updates", {}).get("updatedRange", "")
                start_row = self._extract_start_row(updated_range)

                if start_row:
                    id_values = []
                    for i, item in enumerate(processed_records):
                        if not records[i].get("id"):
                            row_idx = start_row + i
                            item["id"] = self._generate_row_id(row_idx, item["updated_at"])
                        id_values.append([item["id"]])

                    end_row = start_row + len(processed_records) - 1
                    id_range = f"A{start_row}:A{end_row}"
                    ws.update(id_range, id_values)

        return processed_records

    def update_record(self, record_id, updates):
        with self.lock:
            found = self.get_by_id(record_id)
            if not found:
                return None
            
            row_idx = found["row_index"]
            current_data = found["data"]
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            current_data.update(updates)
            current_data["updated_at"] = now_str

            item, row = self._map_record_to_row(current_data, now_str, row_idx=row_idx)
            range_name = f"{rowcol_to_a1(row_idx, 1)}:{rowcol_to_a1(row_idx, len(FIELDNAMES))}"
            ws = self._get_worksheet()
            ws.update(range_name, [row])
            return item


    def delete_record(self, record_id):
        with self.lock:
            found = self.get_by_id(record_id)
            if not found:
                return False
            row_idx = found["row_index"]
            ws = self._get_worksheet()
            ws.delete_rows(row_idx)
            return True
