import os
import sys
import csv
import re
import uuid
import unicodedata
from datetime import datetime
from sheets import (
    GoogleSheetsTable,
    FIELDNAMES,
    generate_invitation_url,
    generate_whatsapp_url,
)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[-\s]+", "_", text)


def split_name(raw_name: str):
    name = raw_name.strip()
    words = name.split()
    if not words:
        return "", ""
    if len(words) == 1:
        return words[0], ""
    if len(words) == 2:
        return words[0], words[1]

    # Check prefixes for compound last names like "del campo", "di maria", "d'ambrosio"
    for i in range(1, len(words)):
        if words[i].lower() in ["del", "de", "di", "da", "d'ambrosio"]:
            return " ".join(words[:i]), " ".join(words[i:])

    # Check common Spanish compound first names
    compound_first_starters = ["ana", "juan", "maria", "maría", "m", "jose", "josé"]
    if words[0].lower() in compound_first_starters and len(words) >= 3:
        return " ".join(words[:2]), " ".join(words[2:])

    return words[0], " ".join(words[1:])


def parse_csv_file(csv_path: str):
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found at: {csv_path}")

    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        raw_rows = list(reader)

    records = []
    main_guests_by_slug = {}
    prev_main_slug = None
    skipped_empty = 0

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for row_idx, r in enumerate(raw_rows, start=2):
        raw_name = r[0].strip() if len(r) > 0 else ""
        if not raw_name:
            skipped_empty += 1
            continue

        dequien = r[1].strip() if len(r) > 1 else ""
        tipo = r[2].strip() if len(r) > 2 else ""
        nivel = r[3].strip() if len(r) > 3 else ""
        # r[4] is 'Enviada' -> ignored per design decision
        confirmacion = r[5].strip() if len(r) > 5 else ""

        # Sanitize Monto tarjeta (ignore counter annotations like '149 invitados', '167 reservas')
        monto = r[6].strip() if len(r) > 6 else ""
        if monto in ["149 invitados", "167 reservas"]:
            monto = ""

        # Sanitize phone numbers
        phone = r[7].strip() if len(r) > 7 else ""
        if phone == "-":
            phone = ""

        # Dieta mapping
        dieta_raw = r[8].strip().lower() if len(r) > 8 else ""
        pa = ""
        celiaco = ""
        if "vegano" in dieta_raw:
            pa = "vegano"
        elif "vegetariano" in dieta_raw:
            pa = "vegetariano"
        elif "general" in dieta_raw:
            pa = "general"

        if "sin tacc" in dieta_raw or "celiaco" in dieta_raw:
            celiaco = "si"

        is_plus_one = "+" in raw_name

        if is_plus_one:
            # Companion: per user instruction, leave first & last names empty
            nombre = ""
            apellido = ""

            # Check if name before + matches a known main guest
            base_name = re.split(r"\+|\(", raw_name)[0].strip()
            base_slug = slugify(base_name)
            if base_slug in main_guests_by_slug:
                inv_id = base_slug
            elif prev_main_slug:
                inv_id = prev_main_slug
            else:
                inv_id = base_slug
        else:
            nombre, apellido = split_name(raw_name)
            inv_id = slugify(f"{nombre}_{apellido}" if apellido else nombre)
            main_guests_by_slug[inv_id] = raw_name
            prev_main_slug = inv_id

        # Unique 8-char guest ID
        guest_id = uuid.uuid4().hex[:8]
        url = generate_invitation_url(inv_id)
        whatsapp = generate_whatsapp_url(phone, url) if phone else ""

        item = {
            "id": guest_id,
            "updated_at": now_str,
            "invitacion_id": inv_id,
            "nombre": nombre,
            "apellido": apellido,
            "dequien": dequien,
            "tipo": tipo,
            "nivel": nivel,
            "confirmacion": confirmacion,
            "montotarjeta": monto,
            "telefono": phone,
            "pa": pa,
            "celiaco": celiaco,
            "url": url,
            "whatsapp": whatsapp,
            "comprobante": "",
            "pagado": "",
            "_raw_name": raw_name,
            "_is_plus_one": is_plus_one,
            "_csv_row": row_idx,
        }
        records.append(item)

    return records, skipped_empty


def import_to_sheets(csv_path: str, dry_run: bool = False):
    records, skipped_empty = parse_csv_file(csv_path)

    companions_count = sum(1 for r in records if r["_is_plus_one"])
    main_count = sum(1 for r in records if not r["_is_plus_one"])
    unique_invs = set(r["invitacion_id"] for r in records)

    print("=" * 60)
    print("           BODA - GUEST LIST IMPORT REPORT")
    print("=" * 60)
    print(f"Source file:             {csv_path}")
    print(f"Skipped empty rows:      {skipped_empty}")
    print(f"Total valid guests:      {len(records)}")
    print(f"  • Main guests:         {main_count}")
    print(f"  • Companions (+1):     {companions_count}")
    print(f"Unique invitation links: {len(unique_invs)}")
    print("=" * 60)

    if dry_run:
        print("\n[DRY RUN MODE] No changes were written to Google Sheets.")
        print("\nSample records (first 5):")
        for r in records[:5]:
            print(f"  Row {r['_csv_row']}: {r['_raw_name']!r:28} -> [{r['invitacion_id']}] nombre={r['nombre']!r}, apellido={r['apellido']!r}, tel={r['telefono']!r}, url={r['url']}")
        print("\nSample companions (first 5):")
        sample_plus = [r for r in records if r["_is_plus_one"]][:5]
        for r in sample_plus:
            print(f"  Row {r['_csv_row']}: {r['_raw_name']!r:28} -> [{r['invitacion_id']}] nombre={r['nombre']!r}, apellido={r['apellido']!r}, tel={r['telefono']!r}, url={r['url']}")
        return records

    print("\nConnecting to Google Sheets...")
    table = GoogleSheetsTable()
    ws = table._get_worksheet()
    table.ensure_header()

    print("Clearing existing records (preserving row 1 headers)...")
    try:
        ws.batch_clear(["A2:Q2000"])
    except Exception as e:
        print(f"Note on batch_clear: {e}")

    # Build matrix for Google Sheets in FIELDNAMES order
    sheet_rows = []
    for r in records:
        row_vals = [str(r.get(col, "")) for col in FIELDNAMES]
        sheet_rows.append(row_vals)

    end_row = 1 + len(sheet_rows)
    target_range = f"A2:Q{end_row}"
    print(f"Writing {len(sheet_rows)} records to range {target_range}...")
    ws.update(range_name=target_range, values=sheet_rows)

    print("Refreshing local JSON cache from Google Sheets...")
    table.get_all(force_remote=True)
    print("Import successfully completed!")
    return records


if __name__ == "__main__":
    csv_file = "/data/lista.csv"
    if not os.path.exists(csv_file):
        csv_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/lista.csv"))

    is_dry_run = "--dry-run" in sys.argv
    import_to_sheets(csv_file, dry_run=is_dry_run)
