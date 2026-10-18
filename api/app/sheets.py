import os
import time
import json
import uuid
import hashlib
import unicodedata
import re
import threading
import urllib.parse
from datetime import datetime
import gspread
from gspread.utils import rowcol_to_a1

FIELDNAMES = [
    "id",
    "updated_at",
    "invitacion_id",
    "nombre",
    "apellido",
    "dequien",
    "tipo",
    "nivel",
    "confirmacion",
    "montotarjeta",
    "telefono",
    "pa",
    "celiaco",
    "url",
    "whatsapp",
    "comprobante",
    "pagado",
    "abierto",
]


INVITATION_SALT = os.environ.get("INVITATION_SALT", "boda_celia_y_alfredo_2027_secret_salt")
BASE_URL = os.environ.get("BASE_URL", "http://nos.vamos.acas.ar")
DEFAULT_RESERVA_MONTO = os.environ.get("DEFAULT_RESERVA_MONTO", "100000")
CACHE_FILE_PATH = os.environ.get("CACHE_FILE_PATH", "/data/sheet_cache.json")
LOG_FILE_PATH = os.environ.get("LOG_FILE_PATH", "/data/api.log")
TTL_READ = int(os.environ.get("TTL_READ", 60))  # 10 minutes default
TTL_WRITE = int(os.environ.get("TTL_WRITE", 60))  # 2 minutes default
CACHE_MISS_MIN_AGE = int(os.environ.get("CACHE_MISS_MIN_AGE", 10))  # Minimum cache age (seconds) to force remote sync on cache miss


def slugify(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[-\s]+", "_", text)


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


def get_group_target_type(guests: list[dict]) -> str:
    """
    Determines whether a group of guests is 'reserva' or 'invitacion'.
    If at least one guest in the invitation has nivel=='reserva' or montotarjeta > 0, returns 'reserva'.
    If all guests have no monto or 0 (and not reserva), returns 'invitacion'.
    """
    for g in (guests or []):
        is_res = (str(g.get("nivel", "") or "").strip().lower() == "reserva")
        raw_monto = re.sub(r"[^\d]", "", str(g.get("montotarjeta", "") or ""))
        has_monto = bool(raw_monto and int(raw_monto) > 0)
        if is_res or has_monto:
            return "reserva"
    return "invitacion"


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


def parse_currency_amount(val) -> float:
    if not val:
        return 0.0
    val_str = str(val).strip()
    if not val_str:
        return 0.0
    val_str = re.sub(r"[^\d,\.]", "", val_str)
    if not val_str:
        return 0.0
    if "," in val_str and "." in val_str:
        val_str = val_str.replace(".", "").replace(",", ".")
    elif "," in val_str:
        val_str = val_str.replace(",", ".")
    elif "." in val_str:
        parts = val_str.split(".")
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            val_str = "".join(parts)
    try:
        return float(val_str)
    except ValueError:
        return 0.0


class GoogleSheetsTable:
    def __init__(self, credentials_path=None, sheet_id=None, worksheet_name=None, cache_file_path=None, log_file_path=None, ttl_read=None, ttl_write=None, cache_miss_min_age=None):
        self.credentials_path = credentials_path or os.environ.get("GOOGLE_CREDENTIALS_PATH", "/secrets/credentials.json")
        self.sheet_id = sheet_id or os.environ.get("GOOGLE_SHEET_ID")
        self.worksheet_name = worksheet_name or os.environ.get("WORKSHEET_NAME", "Respuestas")
        self.cache_file_path = cache_file_path or CACHE_FILE_PATH
        self.log_file_path = log_file_path or LOG_FILE_PATH
        self.ttl_read = ttl_read if ttl_read is not None else TTL_READ
        self.ttl_write = ttl_write if ttl_write is not None else TTL_WRITE
        self.cache_miss_min_age = cache_miss_min_age if cache_miss_min_age is not None else CACHE_MISS_MIN_AGE
        self.lock = threading.RLock()
        self._ws = None
        self._stop_watcher = threading.Event()
        self._watcher_thread = threading.Thread(target=self._file_watcher_loop, daemon=True)
        self._watcher_thread.start()

    def _file_watcher_loop(self):
        while not self._stop_watcher.is_set():
            try:
                if self._check_and_consume_update_trigger():
                    self.get_all(force_remote=True)
                else:
                    cache = self._load_cache()
                    if self._has_expired_local(cache):
                        self.flush_local_to_remote()
                    elif cache and cache.get("last_remote_read") and self._is_read_expired(cache):
                        self.get_all()
            except Exception:
                pass
            self._stop_watcher.wait(2.0)

    def _log_event(self, category: str, source: str, message: str):

        """
        category: "READ", "WRITE", or "SYNC"
        source: "CACHED" or "REMOTE"
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{now_str}] [{category}:{source}] {message}"
        print(log_line, flush=True)
        try:
            log_dir = os.path.dirname(self.log_file_path)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
        except Exception:
            pass

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

    def _get_cache_age(self, cache_data=None):
        if cache_data is None:
            cache_data = self._load_cache()
        if not cache_data or "last_remote_read" not in cache_data:
            return float("inf")
        return time.time() - cache_data.get("last_remote_read", 0)

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
            alim_list = [x.strip().lower() for x in alim.split("|")]
        elif isinstance(alim, list):
            alim_list = [str(x).strip().lower() for x in alim]
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

        nombre_val = str(rec.get("nombre", "") or "").strip()
        apellido_val = str(rec.get("apellido", "") or "").strip()
        inv_val = str(rec.get("invitacion_id") or rec.get("invitacion") or "").strip().replace(" ", "_")

        if not inv_val:
            full_name = f"{nombre_val}_{apellido_val}".strip("_")
            if full_name:
                inv_val = slugify(full_name)

        phone_val = str(rec.get("telefono", "")).strip()

        item["updated_at"] = updated_at
        item["invitacion_id"] = inv_val
        item["invitacion"] = inv_val
        item["nombre"] = nombre_val
        item["apellido"] = apellido_val
        item["dequien"] = str(rec.get("dequien", ""))
        item["tipo"] = str(rec.get("tipo", ""))
        nivel_val = str(rec.get("nivel", "") or "").strip()
        monto_raw = str(rec.get("montotarjeta", "") or "").strip()
        if nivel_val.lower() == "reserva":
            clean_digits = re.sub(r"[^\d]", "", monto_raw)
            if not clean_digits or int(clean_digits) == 0:
                monto_val = DEFAULT_RESERVA_MONTO
            else:
                monto_val = monto_raw
        else:
            monto_val = monto_raw

        item["nivel"] = nivel_val
        item["confirmacion"] = str(rec.get("confirmacion") or rec.get("asistencia", ""))
        item["montotarjeta"] = monto_val
        item["telefono"] = phone_val

        # Dietary preference mapping: general, vegetariano, vegano (single column pa/ap)
        pa_val = str(rec.get("pa") or rec.get("ap") or rec.get("menu") or "").strip().lower()
        if not pa_val:
            if str(rec.get("pa_vegano", "")).lower() in ["si", "true", "1"] or "vegano" in alim_list:
                pa_val = "vegano"
            elif str(rec.get("pa_vegetariano", "")).lower() in ["si", "true", "1"] or "vegetariano" in alim_list:
                pa_val = "vegetariano"
            elif str(rec.get("pa_general", "")).lower() in ["si", "true", "1"] or "general" in alim_list:
                pa_val = "general"

        celiaco_val = str(rec.get("celiaco") or rec.get("pa_celiaco") or "").strip().lower()
        if not celiaco_val and "celiaco" in alim_list:
            celiaco_val = "si"

        item["pa"] = pa_val
        item["ap"] = pa_val
        item["celiaco"] = "si" if celiaco_val in ["si", "true", "1"] else ""
        # Backwards-compatibility aliases for templates
        item["pa_celiaco"] = item["celiaco"]
        item["pa_general"] = "si" if pa_val == "general" else ""
        item["pa_vegetariano"] = "si" if pa_val == "vegetariano" else ""
        item["pa_vegano"] = "si" if pa_val == "vegano" else ""

        # Check URL validity for inv_val
        url_val = str(rec.get("url", "") or "").strip()
        normalized_slug = str(inv_val).strip().lower().replace(" ", "_").replace("-", "_") if inv_val else ""

        url_is_valid = False
        if url_val and normalized_slug:
            token = url_val.rstrip("/").split("/")[-1]
            if parse_and_validate_token(token) == normalized_slug:
                url_is_valid = True

        if inv_val and not url_is_valid:
            item["url"] = generate_invitation_url(inv_val)
        else:
            item["url"] = url_val

        # Check WhatsApp validity
        wa_val = str(rec.get("whatsapp", "") or "").strip()
        expected_wa = generate_whatsapp_url(phone_val, item["url"]) if (phone_val and item.get("url")) else ""

        if expected_wa:
            clean_phone = clean_phone_number(phone_val)
            if not wa_val or not url_is_valid or (clean_phone and f"/{clean_phone}?" not in wa_val):
                item["whatsapp"] = expected_wa
            else:
                item["whatsapp"] = wa_val
        else:
            item["whatsapp"] = "" if not phone_val else wa_val

        item["comprobante"] = str(rec.get("comprobante", "") or "")
        item["pagado"] = str(rec.get("pagado", "") or "").strip()
        abierto_val = str(rec.get("abierto", "") or "").strip().lower()
        item["abierto"] = "si" if abierto_val in ("si", "sí", "true", "1") else ""


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

            # Dynamically resolve current row numbers by matching Column A ("id") in Google Sheets
            # This ensures that if rows were reordered/sorted in Google Sheets, updates still match the exact row by ID!
            try:
                id_col_values = ws.col_values(1)
                id_to_current_row = {
                    val.strip(): r
                    for r, val in enumerate(id_col_values, start=1)
                    if val and val.strip()
                }
            except Exception:
                id_to_current_row = {}

            batch_updates = []
            new_appends = []

            for rec in local_records:
                rec_id = str(rec.get("id", "")).strip()
                current_row = id_to_current_row.get(rec_id, rec.get("_sheet_row"))

                if current_row and int(current_row) >= 2:
                    rec["_sheet_row"] = current_row
                    item, row = self._map_record_to_row(rec, now_str, row_idx=current_row)
                    range_name = f"{rowcol_to_a1(current_row, 1)}:{rowcol_to_a1(current_row, len(FIELDNAMES))}"
                    batch_updates.append({"range": range_name, "values": [row]})
                else:
                    item, row = self._map_record_to_row(rec, now_str, row_idx=None)
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
            self._log_event(
                "WRITE",
                "REMOTE",
                f"Flushed {len(local_records)} dirty LOCAL record(s) to Google Sheets via ws.batch_update() [1 HTTPS call]"
            )
            return len(local_records)

    def _sync_from_remote(self, existing_cache=None):
        with self.lock:
            if existing_cache is None:
                existing_cache = self._load_cache()

            # Bidirectional sync: Flush ALL pending LOCAL records to Google Sheets first
            if existing_cache and any(r.get("_sync_state") == "LOCAL" for r in existing_cache.get("records", [])):
                self.flush_local_to_remote()
                existing_cache = self._load_cache()

            ws = self._get_worksheet()
            try:
                remote_records = ws.get_all_records(expected_headers=FIELDNAMES, numericise_ignore=["all"])
            except Exception:
                remote_records = ws.get_all_records(numericise_ignore=["all"])
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

                    id_missing = not rec.get("id")
                    inv_changed = (item.get("invitacion_id", "") != str(rec.get("invitacion_id", "") or "").strip())
                    url_changed = (item.get("url", "") != str(rec.get("url", "") or "").strip())
                    wa_changed = (item.get("whatsapp", "") != str(rec.get("whatsapp", "") or "").strip())
                    monto_changed = (item.get("montotarjeta", "") != str(rec.get("montotarjeta", "") or "").strip())
                    if id_missing or inv_changed or url_changed or wa_changed or monto_changed:
                        range_name = f"{rowcol_to_a1(idx, 1)}:{rowcol_to_a1(idx, len(FIELDNAMES))}"
                        missing_updates.append({"range": range_name, "values": [row]})

            if missing_updates:
                ws.batch_update(missing_updates)
                self._log_event("SYNC", "REMOTE", f"Repaired {len(missing_updates)} records missing IDs/URLs/Montos on Google Sheets")

            new_cache = {
                "last_remote_read": time.time(),
                "records": merged_records
            }
            self._save_cache(new_cache)
            self._log_event("READ", "REMOTE", f"Fetched {len(remote_records)} records from Google Sheets (get_all_records)")
            return merged_records

    def _check_and_consume_update_trigger(self) -> bool:
        try:
            cache_dir = os.path.dirname(self.cache_file_path)
            for fname in ["update_now", "update.now"]:
                trigger_path = os.path.join(cache_dir, fname) if cache_dir else fname
                if os.path.exists(trigger_path):
                    try:
                        os.remove(trigger_path)
                    except Exception as e:
                        print(f"Error removing trigger file: {e}")
                    self._log_event(
                        "SYNC",
                        "TRIGGER",
                        f"Detected '{fname}' trigger file. Triggering bidirectional sync (flush local writes + pull remote changes)"
                    )
                    return True
        except Exception:
            pass
        return False



    def ensure_ids(self):
        with self.lock:
            records = self.get_all(force_remote=True)
            return records, 0

    def get_all(self, force_remote=False):
        with self.lock:
            trigger_fired = self._check_and_consume_update_trigger()
            cache = self._load_cache()
            if cache is None or force_remote or trigger_fired or self._is_read_expired(cache):
                return self._sync_from_remote(existing_cache=cache)

            if self._has_expired_local(cache):
                self.flush_local_to_remote()
                cache = self._load_cache()

            age = time.time() - cache.get("last_remote_read", 0)
            self._log_event("READ", "CACHED", f"Served {len(cache['records'])} records from local JSON cache (age: {age:.1f}s / {self.ttl_read}s)")
            return cache["records"]


    def get_by_id(self, record_id):
        records = self.get_all()
        for rec in records:
            if str(rec.get("id")) == str(record_id):
                return {"row_index": rec.get("_sheet_row"), "data": rec}
        # Cache miss: only force remote sync if cache is older than cache_miss_min_age (e.g. 10s)
        cache_age = self._get_cache_age()
        if cache_age >= self.cache_miss_min_age:
            records = self.get_all(force_remote=True)
            for rec in records:
                if str(rec.get("id")) == str(record_id):
                    self._log_event("SYNC", "REMOTE", f"Cache miss for record id '{record_id}'. Synced from Google Sheets and found record.")
                    return {"row_index": rec.get("_sheet_row"), "data": rec}
        else:
            self._log_event("READ", "CACHED", f"Cache miss for record id '{record_id}', skipping remote sync (cache age: {cache_age:.1f}s < {self.cache_miss_min_age}s)")
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
        # Cache miss: only force remote sync if cache is older than cache_miss_min_age (e.g. 10s)
        if not matched:
            cache_age = self._get_cache_age()
            if cache_age >= self.cache_miss_min_age:
                records = self.get_all(force_remote=True)
                matched = [
                    rec for rec in records
                    if str(rec.get("invitacion_id") or rec.get("invitacion") or "").strip().lower().replace(" ", "_").replace("-", "_") == target
                ]
                if not matched:
                    matched = [
                        rec for rec in records
                        if str(rec.get("id", "")).strip().lower() == target
                    ]
                if matched:
                    self._log_event("SYNC", "REMOTE", f"Cache miss for '{invitacion_id}'. Synced from Google Sheets and found {len(matched)} record(s).")
            else:
                self._log_event("READ", "CACHED", f"Cache miss for '{invitacion_id}', skipping remote sync (cache age: {cache_age:.1f}s < {self.cache_miss_min_age}s)")
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
            self._log_event("WRITE", "CACHED", f"Added {len(added)} new record(s) staged locally (status: LOCAL)")

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
                cache_age = self._get_cache_age(cache)
                if cache_age >= self.cache_miss_min_age:
                    self.get_all(force_remote=True)
                    cache = self._load_cache()
                    for rec in cache.get("records", []):
                        if str(rec.get("id")) == str(record_id):
                            found_rec = rec
                            break
                else:
                    self._log_event("WRITE", "CACHED", f"Cache miss for update '{record_id}', skipping remote sync (cache age: {cache_age:.1f}s < {self.cache_miss_min_age}s)")

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
            self._log_event(
                "WRITE",
                "CACHED",
                f"Updated guest '{record_id}' ({found_rec.get('nombre', '')} {found_rec.get('apellido', '')}) locally -> status: LOCAL, fields: {list(updates.keys())}"
            )

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
            self._log_event("WRITE", "REMOTE", f"Deleted guest '{record_id}' (row {row_idx}) from Google Sheets and local cache")
            return True

    def mark_opened(self, invitacion_id: str) -> bool:
        """
        Marks all guests in an invitation group as abierto='si' when visited by a human.
        Updates cache locally and schedules an asynchronous flush to Google Sheets.
        """
        if not invitacion_id:
            return False

        with self.lock:
            cache = self._load_cache()
            if not cache or not cache.get("records"):
                return False

            target = str(invitacion_id).strip().lower().replace(" ", "_").replace("-", "_")
            needs_update = False
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for rec in cache["records"]:
                inv_slug = str(rec.get("invitacion_id") or rec.get("invitacion") or rec.get("id") or "").strip().lower().replace(" ", "_").replace("-", "_")
                if inv_slug == target:
                    if str(rec.get("abierto", "")).strip().lower() != "si":
                        rec["abierto"] = "si"
                        rec["updated_at"] = now_str
                        rec["_cached_at"] = time.time()
                        rec["_sync_state"] = "LOCAL"
                        needs_update = True

            if needs_update:
                self._save_cache(cache)
                self._log_event("WRITE", "CACHED", f"Marked invitation '{invitacion_id}' as abierto=si")

                def _bg_flush():
                    try:
                        self.flush_local_to_remote()
                    except Exception as e:
                        self._log_event("WRITE", "ERROR", f"Error flushing abierto status to Google Sheets: {e}")

                threading.Thread(target=_bg_flush, daemon=True).start()
                return True

            return False

