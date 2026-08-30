import os
import time
import json
import uuid
import hashlib
import re
import threading
import urllib.parse
from datetime import datetime
import gspread
from gspread.utils import rowcol_to_a1

FIELDNAMES = [
    "id",
    "updated_at",
    "nombre",
    "apellido",
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
    "whatsapp",
]


INVITATION_SALT = os.environ.get("INVITATION_SALT", "boda_celia_y_alfredo_2027_secret_salt")
BASE_URL = os.environ.get("BASE_URL", "http://nos.vamos.acas.ar")
CACHE_FILE_PATH = os.environ.get("CACHE_FILE_PATH", "/data/sheet_cache.json")
TTL_READ = int(os.environ.get("TTL_READ", 600))  # 10 minutes default
TTL_WRITE = int(os.environ.get("TTL_WRITE", 120))  # 2 minutes default


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


def clean_phone_number(phone: str) -> str:
    if not phone:
        return ""
    return re.sub(r"[^\d]", "", str(phone))


def generate_whatsapp_url(phone: str, url: str) -> str:
    if not phone or not url:
        return ""
    clean_phone = clean_phone_number(phone)
    if not clean_phone:
        return ""
    encoded_text = urllib.parse.quote_plus(url)
    return f"https://wa.me/{clean_phone}?text={encoded_text}"


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
    def __init__(self, credentials_path=None, sheet_id=None, worksheet_name=None, cache_file_path=None, ttl_read=None, ttl_write=None):
        self.credentials_path = credentials_path or os.environ.get("GOOGLE_CREDENTIALS_PATH", "/secrets/credentials.json")
        self.sheet_id = sheet_id or os.environ.get("GOOGLE_SHEET_ID")
        self.worksheet_name = worksheet_name or os.environ.get("WORKSHEET_NAME", "Respuestas")
        self.cache_file_path = cache_file_path or CACHE_FILE_PATH
        self.ttl_read = ttl_read if ttl_read is not None else TTL_READ
        self.ttl_write = ttl_write if ttl_write is not None else TTL_WRITE
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

    def _load_cache(self):
        if not os.path.exists(self.cache_file_path):
            return None
        try:
            with open(self.cache_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "records" in data and "last_remote_read" in data:
                    return data
        except Exception:
            pass
        return None

    def _save_cache(self, cache_data):
        try:
            cache_dir = os.path.dirname(self.cache_file_path)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir, exist_ok=True)
            temp_path = f"{self.cache_file_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.cache_file_path)
        except Exception as e:
            print(f"Error saving cache: {e}")

    def _has_expired_local(self, cache_data):
        if not cache_data or not cache_data.get("records"):
            return False
        now = time.time()
        for rec in cache_data["records"]:
            if rec.get("_sync_state") == "LOCAL":
                cached_at = rec.get("_cached_at", 0)
                if (now - cached_at) >= self.ttl_write:
                    return True
        return False

    def _is_read_expired(self, cache_data):
        if not cache_data:
            return True
        last_read = cache_data.get("last_remote_read", 0)
        return (time.time() - last_read) >= self.ttl_read

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
        phone_val = str(rec.get("telefono", "")).strip()

        item["updated_at"] = updated_at
        item["nombre"] = str(rec.get("nombre", ""))
        item["apellido"] = str(rec.get("apellido", ""))
        item["telefono"] = phone_val
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

        if not item.get("whatsapp") and phone_val and item.get("url"):
            item["whatsapp"] = generate_whatsapp_url(phone_val, item["url"])
        else:
            item["whatsapp"] = str(item.get("whatsapp", ""))

        # Build row according to FIELDNAMES
        row = []
        for col in FIELDNAMES:
            row.append(str(item.get(col, "")))

        return item, row

    def flush_local_to_remote(self):
        with self.lock:
            cache = self._load_cache()
            if not cache or not cache.get("records"):
                return 0

            local_records = [r for r in cache["records"] if r.get("_sync_state") == "LOCAL"]
            if not local_records:
                return 0

            ws = self._get_worksheet()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            batch_updates = []
            new_appends = []

            for rec in local_records:
                item, row = self._map_record_to_row(rec, now_str, row_idx=rec.get("_sheet_row"))
                row_idx = rec.get("_sheet_row")
                if row_idx and int(row_idx) >= 2:
                    range_name = f"{rowcol_to_a1(row_idx, 1)}:{rowcol_to_a1(row_idx, len(FIELDNAMES))}"
                    batch_updates.append({"range": range_name, "values": [row]})
                else:
                    new_appends.append((rec, row))

            if batch_updates:
                ws.batch_update(batch_updates)

            if new_appends:
                rows_to_insert = [r for _, r in new_appends]
                res = ws.append_rows(rows_to_insert, value_input_option="USER_ENTERED")
                start_row = None
                if isinstance(res, dict):
                    updated_range = res.get("updates", {}).get("updatedRange", "")
                    start_row = self._extract_start_row(updated_range)
                if start_row:
                    for i, (rec, _) in enumerate(new_appends):
                        rec["_sheet_row"] = start_row + i

            for rec in local_records:
                rec["_sync_state"] = "REMOTE"

            self._save_cache(cache)
            return len(local_records)

    def _sync_from_remote(self, existing_cache=None):
        with self.lock:
            if existing_cache and self._has_expired_local(existing_cache):
                self.flush_local_to_remote()
                existing_cache = self._load_cache()

            ws = self._get_worksheet()
            remote_records = ws.get_all_records()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            local_by_id = {}
            if existing_cache and existing_cache.get("records"):
                for r in existing_cache["records"]:
                    if r.get("_sync_state") == "LOCAL" and r.get("id"):
                        local_by_id[str(r["id"])] = r

            merged_records = []
            missing_updates = []

            for idx, rec in enumerate(remote_records, start=2):
                item, row = self._map_record_to_row(rec, now_str, row_idx=idx)
                rec_id = str(item.get("id", ""))

                if rec_id in local_by_id:
                    local_rec = local_by_id[rec_id]
                    local_rec["_sheet_row"] = idx
                    merged_records.append(local_rec)
                else:
                    item["_sync_state"] = "REMOTE"
                    item["_cached_at"] = time.time()
                    item["_sheet_row"] = idx
                    merged_records.append(item)

                    if not rec.get("id") or not rec.get("url") or not rec.get("whatsapp"):
                        range_name = f"{rowcol_to_a1(idx, 1)}:{rowcol_to_a1(idx, len(FIELDNAMES))}"
                        missing_updates.append({"range": range_name, "values": [row]})

            if missing_updates:
                ws.batch_update(missing_updates)

            new_cache = {
                "last_remote_read": time.time(),
                "records": merged_records
            }
            self._save_cache(new_cache)
            return merged_records

    def ensure_ids(self):
        with self.lock:
            records = self.get_all(force_remote=True)
            return records, 0

    def get_all(self, force_remote=False):
        with self.lock:
            cache = self._load_cache()
            if cache is None or force_remote or self._is_read_expired(cache):
                return self._sync_from_remote(existing_cache=cache)

            if self._has_expired_local(cache):
                self.flush_local_to_remote()
                cache = self._load_cache()

            return cache["records"]

    def get_by_id(self, record_id):
        records = self.get_all()
        for rec in records:
            if str(rec.get("id")) == str(record_id):
                return {"row_index": rec.get("_sheet_row"), "data": rec}
        return None

    def get_by_invitacion(self, invitacion_id):
        if not invitacion_id:
            return []
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

    def add_records(self, records):
        with self.lock:
            cache = self._load_cache()
            if cache is None:
                self.get_all()
                cache = self._load_cache()

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            existing_records = cache.get("records", [])
            max_row = max([r.get("_sheet_row", 1) for r in existing_records], default=1)

            added = []
            for i, rec in enumerate(records):
                next_row = max_row + 1 + i
                item, _ = self._map_record_to_row(rec, now_str, row_idx=next_row)
                item["_cached_at"] = time.time()
                item["_sync_state"] = "LOCAL"
                item["_sheet_row"] = next_row
                existing_records.append(item)
                added.append(item)

            self._save_cache(cache)

            if self._has_expired_local(cache):
                self.flush_local_to_remote()

            return added

    def update_record(self, record_id, updates):
        with self.lock:
            cache = self._load_cache()
            if cache is None:
                self.get_all()
                cache = self._load_cache()

            found_rec = None
            for rec in cache.get("records", []):
                if str(rec.get("id")) == str(record_id):
                    found_rec = rec
                    break

            if not found_rec:
                self.get_all(force_remote=True)
                cache = self._load_cache()
                for rec in cache.get("records", []):
                    if str(rec.get("id")) == str(record_id):
                        found_rec = rec
                        break

            if not found_rec:
                return None

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            found_rec.update(updates)
            found_rec["updated_at"] = now_str
            found_rec["_cached_at"] = time.time()
            found_rec["_sync_state"] = "LOCAL"

            item, _ = self._map_record_to_row(found_rec, now_str, row_idx=found_rec.get("_sheet_row"))
            item["_cached_at"] = found_rec["_cached_at"]
            item["_sync_state"] = "LOCAL"
            item["_sheet_row"] = found_rec.get("_sheet_row")
            found_rec.update(item)

            self._save_cache(cache)

            if self._has_expired_local(cache):
                self.flush_local_to_remote()

            return found_rec

    def delete_record(self, record_id):
        with self.lock:
            found = self.get_by_id(record_id)
            if not found:
                return False

            row_idx = found.get("row_index")
            if row_idx:
                ws = self._get_worksheet()
                ws.delete_rows(row_idx)

            cache = self._load_cache()
            if cache and cache.get("records"):
                cache["records"] = [r for r in cache["records"] if str(r.get("id")) != str(record_id)]
                if row_idx:
                    for r in cache["records"]:
                        if r.get("_sheet_row") and r["_sheet_row"] > row_idx:
                            r["_sheet_row"] -= 1
                self._save_cache(cache)
            return True
