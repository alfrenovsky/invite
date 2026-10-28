import os
import time
import unittest
from unittest.mock import MagicMock, patch
from sheets import (
    GoogleSheetsTable,
    FIELDNAMES,
    BASE_URL,
    slugify,
    compute_check_code,
    generate_invitation_url,
    parse_and_validate_token,
    clean_phone_number,
    generate_whatsapp_url,
    parse_currency_amount,
    get_group_target_type,
    DEFAULT_RESERVA_MONTO,
)


def get_panel_auth_headers(user="ari", password="gato"):
    import base64
    creds = f"{user}:{password}".encode("utf-8")
    return {"Authorization": f"Basic {base64.b64encode(creds).decode('ascii')}"}


class TestGoogleSheetsTable(unittest.TestCase):
    def setUp(self):
        self.cache_path = "/tmp/test_cache_unittest.json"
        if os.path.exists(self.cache_path):
            os.remove(self.cache_path)

        self.patcher = patch("sheets.gspread.service_account")
        self.mock_sa = self.patcher.start()

        self.mock_gc = MagicMock()
        self.mock_sh = MagicMock()
        self.mock_ws = MagicMock()

        self.mock_sa.return_value = self.mock_gc
        self.mock_gc.open_by_key.return_value = self.mock_sh
        self.mock_sh.worksheet.return_value = self.mock_ws
        self.mock_ws.row_values.return_value = FIELDNAMES
        self.mock_ws.append_rows.return_value = {"updates": {"updatedRange": "Respuestas!A2:L2"}}

        self.table = GoogleSheetsTable(
            credentials_path="/dummy/path",
            sheet_id="dummy_sheet_id",
            cache_file_path=self.cache_path,
            ttl_read=600,
            ttl_write=120,
        )
        try:
            from app import table as app_table
            app_table.cache_file_path = self.cache_path
        except Exception:
            pass

    def tearDown(self):
        self.patcher.stop()
        if os.path.exists(self.cache_path):
            os.remove(self.cache_path)

    def test_get_all_and_cache(self):
        sample_data = [
            {"id": "abc12345", "apellido": "Perez", "nombre": "Juan", "confirmacion": "si", "pa_general": "si", "url": "http://test/i/perez_123", "whatsapp": ""}
        ]
        self.mock_ws.get_all_records.return_value = sample_data
        
        # 1st read: fetches from remote
        result = self.table.get_all()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["nombre"], "Juan")
        self.assertEqual(self.mock_ws.get_all_records.call_count, 1)

        # 2nd read: serves from local JSON cache (0 remote calls!)
        result2 = self.table.get_all()
        self.assertEqual(len(result2), 1)
        self.assertEqual(self.mock_ws.get_all_records.call_count, 1)

    def test_update_now_trigger_file(self):
        sample_data = [
            {"id": "abc12345", "apellido": "Perez", "nombre": "Juan", "confirmacion": "si", "pa_general": "si", "url": "http://test/i/perez_123", "whatsapp": ""}
        ]
        self.mock_ws.get_all_records.return_value = sample_data
        
        # 1. Initial read into cache
        self.table.get_all()
        self.assertEqual(self.mock_ws.get_all_records.call_count, 1)

        # 2. Add a local write (only 1 second old, not expired)
        self.table.update_record("abc12345", {"confirmacion": "no"})
        cache = self.table._load_cache()
        self.assertEqual(cache["records"][0]["_sync_state"], "LOCAL")
        self.mock_ws.batch_update.reset_mock()

        # 3. Place update_now trigger file in cache directory
        trigger_path = os.path.join(os.path.dirname(self.cache_path), "update_now")
        with open(trigger_path, "w") as f:
            f.write("1")
        self.assertTrue(os.path.exists(trigger_path))

        # 4. Next get_all should flush local write to Google Sheets AND refresh from remote (bidirectional)
        self.table.get_all()
        self.assertFalse(os.path.exists(trigger_path))
        self.assertGreaterEqual(self.mock_ws.batch_update.call_count, 1)
        self.assertEqual(self.mock_ws.get_all_records.call_count, 2)



    def test_get_by_id(self):
        sample_data = [
            {"id": "abc12345", "apellido": "Perez", "nombre": "Juan", "confirmacion": "si", "url": "http://test/i/1", "whatsapp": ""},
            {"id": "def67890", "apellido": "Gomez", "nombre": "Maria", "confirmacion": "no", "url": "http://test/i/2", "whatsapp": ""}
        ]
        self.mock_ws.get_all_records.return_value = sample_data

        found = self.table.get_by_id("def67890")
        self.assertIsNotNone(found)
        self.assertEqual(found["row_index"], 3)
        self.assertEqual(found["data"]["nombre"], "Maria")

    def test_cache_miss_forces_remote_sync(self):
        # Initial cache only has one group
        self.mock_ws.get_all_records.return_value = [
            {"id": "abc12345", "invitacion_id": "familia_perez", "nombre": "Juan", "apellido": "Perez"}
        ]
        self.table.get_all()
        self.assertEqual(self.mock_ws.get_all_records.call_count, 1)

        # A new guest group is added directly to Google Sheets
        self.mock_ws.get_all_records.return_value = [
            {"id": "abc12345", "invitacion_id": "familia_perez", "nombre": "Juan", "apellido": "Perez"},
            {"id": "cel12345", "invitacion_id": "celia_y_alfredo", "nombre": "Celia", "apellido": "G"}
        ]

        # Simulate cache being older than cache_miss_min_age (10s)
        cache = self.table._load_cache()
        cache["last_remote_read"] = time.time() - 15
        self.table._save_cache(cache)

        # Querying the new group causes a cache miss, which triggers a remote sync from Google Sheets
        found = self.table.get_by_invitacion("celia_y_alfredo")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["nombre"], "Celia")
        self.assertEqual(self.mock_ws.get_all_records.call_count, 2)

    def test_cache_miss_fresh_cache_skips_remote_sync(self):
        # Initial cache only has one group
        self.mock_ws.get_all_records.return_value = [
            {"id": "abc12345", "invitacion_id": "familia_perez", "nombre": "Juan", "apellido": "Perez"}
        ]
        self.table.get_all()
        self.assertEqual(self.mock_ws.get_all_records.call_count, 1)

        # Cache is fresh (< 10 seconds old). Querying missing group should NOT call remote.
        found = self.table.get_by_invitacion("celia_y_alfredo")
        self.assertEqual(found, [])
        self.assertEqual(self.mock_ws.get_all_records.call_count, 1)

        # Querying missing id should NOT call remote either.
        found_id = self.table.get_by_id("non_existent_id")
        self.assertIsNone(found_id)
        self.assertEqual(self.mock_ws.get_all_records.call_count, 1)


    def test_add_records(self):
        self.mock_ws.get_all_records.return_value = []
        new_record = {"apellido": "Lopez", "nombre": "Carlos", "telefono": "+54 9 11 1234-5678", "asistencia": "si", "alimentacion": ["celiaco"], "invitacion_id": "amigos"}
        res = self.table.add_records([new_record])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["apellido"], "Lopez")
        self.assertEqual(res[0]["pa_celiaco"], "si")
        self.assertEqual(len(res[0]["id"]), 8)
        self.assertTrue(res[0]["url"].startswith(f"{BASE_URL}/amigos_"))
        self.assertTrue(res[0]["whatsapp"].startswith("https://wa.me/5491112345678?text="))

    def test_update_record_and_batch_flush(self):
        sample_data = [
            {"id": "abc12345", "apellido": "Perez", "nombre": "Juan", "confirmacion": "si", "url": "http://test/i/1", "whatsapp": ""}
        ]
        self.mock_ws.get_all_records.return_value = sample_data

        # 1. Initial read
        self.table.get_all()
        self.mock_ws.batch_update.reset_mock()

        # 2. Local update (< 2 min old) -> does NOT call batch_update immediately
        updated = self.table.update_record("abc12345", {"confirmacion": "no"})
        self.assertIsNotNone(updated)
        self.assertEqual(updated["confirmacion"], "no")
        self.assertEqual(updated["_sync_state"], "LOCAL")
        self.assertEqual(self.mock_ws.batch_update.call_count, 0)

        # 3. Simulate TTL_WRITE expired (>= 2 min)
        cache = self.table._load_cache()
        cache["records"][0]["_cached_at"] = time.time() - 150
        self.table._save_cache(cache)

        # 4. Trigger flush on next operation
        self.table.get_all()
        self.assertEqual(self.mock_ws.batch_update.call_count, 1)

        # 5. Verify marked as REMOTE
        cache_after = self.table._load_cache()
        self.assertEqual(cache_after["records"][0]["_sync_state"], "REMOTE")

    def test_flush_handles_reordered_rows_by_id(self):
        # 1. Initially guest was on row 2
        sample_data = [
            {"id": "abc12345", "apellido": "Perez", "nombre": "Juan", "confirmacion": "si"}
        ]
        self.mock_ws.get_all_records.return_value = sample_data
        self.table.get_all()

        # 2. Guest updates RSVP locally
        self.table.update_record("abc12345", {"confirmacion": "no"})

        # 3. Meanwhile, the sheet was sorted/reordered in Google Sheets so 'abc12345' is now on row 5!
        self.mock_ws.col_values.return_value = ["id", "other_1", "other_2", "other_3", "abc12345"]
        self.mock_ws.batch_update.reset_mock()

        # 4. Flush runs
        self.table.flush_local_to_remote()

        # 5. Assert batch_update was sent to Row 5 (range A5:O5), NOT the old row 2!
        self.assertEqual(self.mock_ws.batch_update.call_count, 1)
        update_call_args = self.mock_ws.batch_update.call_args[0][0]
        self.assertTrue(update_call_args[0]["range"].startswith("A5:"))


    def test_delete_record(self):
        sample_data = [
            {"id": "abc12345", "apellido": "Perez", "nombre": "Juan", "confirmacion": "si", "url": "http://test/i/1", "whatsapp": ""}
        ]
        self.mock_ws.get_all_records.return_value = sample_data

        deleted = self.table.delete_record("abc12345")
        self.assertTrue(deleted)
        self.mock_ws.delete_rows.assert_called_with(2)

    def test_ensure_ids_and_urls_and_whatsapp(self):
        sample_data = [
            {"id": "", "url": "", "whatsapp": "", "updated_at": "2026-08-12 12:00:00", "apellido": "Perez", "nombre": "Juan", "telefono": "+54 9 11 9876-5432", "invitacion_id": "familia_perez"},
            {"id": "abc12345", "url": "http://nos.vamos.acas.ar/i/test_123456", "whatsapp": "https://wa.me/5491111111111?text=test", "apellido": "Gomez", "nombre": "Maria", "confirmacion": "si"}
        ]
        self.mock_ws.get_all_records.return_value = sample_data

        records, _ = self.table.ensure_ids()
        self.assertEqual(len(records[0]["id"]), 8)
        self.assertTrue(records[0]["url"].startswith(f"{BASE_URL}/familia_perez_"))
        self.assertTrue(records[0]["whatsapp"].startswith("https://wa.me/5491198765432?text="))

    def test_slugify(self):
        self.assertEqual(slugify("Juan Pérez"), "juan_perez")
        self.assertEqual(slugify("  María José  "), "maria_jose")
        self.assertEqual(slugify("Ángel O'Connor!"), "angel_oconnor")
        self.assertEqual(slugify(""), "")

    def test_empty_invitacion_id_autofills_firstname_lastname(self):
        sample_data = [
            {"id": "abc12345", "invitacion_id": "", "url": "", "whatsapp": "", "apellido": "Pérez", "nombre": "Juan", "telefono": "+54 9 11 9876-5432"},
            {"id": "def67890", "invitacion_id": "ya_tiene_id", "url": "", "whatsapp": "", "apellido": "Gómez", "nombre": "María"},
            {"id": "ghi11111", "invitacion_id": "", "url": "", "whatsapp": "", "apellido": "", "nombre": "SingleName"},
            {"id": "jkl22222", "invitacion_id": "", "url": "", "whatsapp": "", "apellido": "", "nombre": ""}
        ]
        self.mock_ws.get_all_records.return_value = sample_data

        records = self.table.get_all()
        # Row 1: autofilled with "juan_perez"
        self.assertEqual(records[0]["invitacion_id"], "juan_perez")
        self.assertEqual(records[0]["invitacion"], "juan_perez")
        self.assertTrue(records[0]["url"].startswith(f"{BASE_URL}/juan_perez_"))
        self.assertTrue(records[0]["whatsapp"].startswith("https://wa.me/5491198765432?text="))

        # Row 2: already had invitacion_id, preserved
        self.assertEqual(records[1]["invitacion_id"], "ya_tiene_id")

        # Row 3: name only, autofilled with "singlename"
        self.assertEqual(records[2]["invitacion_id"], "singlename")
        self.assertTrue(records[2]["url"].startswith(f"{BASE_URL}/singlename_"))

        # Row 4: empty name and surname, remains empty
        self.assertEqual(records[3]["invitacion_id"], "")

        # Verify Google Sheets remote repair update triggered for modified rows
        self.assertEqual(self.mock_ws.batch_update.call_count, 1)
        update_calls = self.mock_ws.batch_update.call_args[0][0]
        ranges = [c["range"] for c in update_calls]
        self.assertTrue(any(r.startswith("A2:") for r in ranges))
        self.assertTrue(any(r.startswith("A4:") for r in ranges))

    def test_deterministic_row_time_id(self):
        self.mock_ws.get_all_records.return_value = []
        res = self.table.add_records([{"nombre": "Test", "updated_at": "2026-08-12 12:00:00"}])
        expected_id = self.table._generate_row_id(2, "2026-08-12 12:00:00")
        self.assertEqual(res[0]["id"], expected_id)

    def test_get_by_invitacion(self):
        sample_data = [
            {"id": "id1", "apellido": "Perez", "nombre": "Juan", "invitacion": "Familia Perez", "url": "http://test/1", "whatsapp": ""},
            {"id": "id2", "apellido": "Perez", "nombre": "Maria", "invitacion": "Familia Perez", "url": "http://test/2", "whatsapp": ""},
            {"id": "id3", "apellido": "Gomez", "nombre": "Carlos", "invitacion": "amigos_facu", "url": "http://test/3", "whatsapp": ""}
        ]
        self.mock_ws.get_all_records.return_value = sample_data
        
        group1 = self.table.get_by_invitacion("familia_perez")
        self.assertEqual(len(group1), 2)
        self.assertEqual(group1[0]["nombre"], "Juan")

        group2 = self.table.get_by_invitacion("Familia Perez")
        self.assertEqual(len(group2), 2)

        group3 = self.table.get_by_invitacion("familia-perez")
        self.assertEqual(len(group3), 2)

    def test_check_code_and_token_validation(self):
        inv_id = "familia_rodriguez"
        code = compute_check_code(inv_id)
        self.assertEqual(len(code), 6)

        base_url = os.environ.get("BASE_URL", "http://nos.vamos.acas.ar")
        url = generate_invitation_url(inv_id)
        self.assertEqual(url, f"{base_url}/familia_rodriguez_{code}")

        valid_slug = parse_and_validate_token(f"familia_rodriguez_{code}")
        self.assertEqual(valid_slug, "familia_rodriguez")

        invalid_slug = parse_and_validate_token("familia_rodriguez_ffffff")
        self.assertIsNone(invalid_slug)

        no_code_slug = parse_and_validate_token("familia_rodriguez")
        self.assertIsNone(no_code_slug)

    def test_whatsapp_url_generation(self):
        phone = "+54 9 (11) 2345-6789"
        url = "http://nos.vamos.acas.ar/i/test_123456"
        clean = clean_phone_number(phone)
        self.assertEqual(clean, "5491123456789")

        wa_url = generate_whatsapp_url(phone, url)
        self.assertEqual(wa_url, "https://wa.me/5491123456789?text=http%3A%2F%2Fnos.vamos.acas.ar%2Fi%2Ftest_123456")

        self.assertEqual(generate_whatsapp_url("", url), "")

    def test_index_page_rendering(self):
        from app import app
        with patch("app.table.get_by_invitacion") as mock_get_by_inv:
            mock_get_by_inv.return_value = [
                {"id": "abc1", "nombre": "Juan", "apellido": "Perez", "confirmacion": "si", "url": "http://nos.vamos.acas.ar/i/familia_perez_123456"}
            ]
            with app.test_client() as client:
                code = compute_check_code("familia_perez")
                res = client.get(f"/i/familia_perez_{code}")
                self.assertEqual(res.status_code, 200)
                self.assertIn(b"Celia", res.data)

    def test_whatsapp_crawler_preview_bypass(self):
        from app import app
        with patch("app.table.get_by_invitacion") as mock_get_by_inv:
            with app.test_client() as client:
                code = compute_check_code("familia_perez")
                headers = {"User-Agent": "WhatsApp/2.21.12.21 A"}
                res = client.get(f"/i/familia_perez_{code}", headers=headers)
                self.assertEqual(res.status_code, 200)
                mock_get_by_inv.assert_not_called()
                self.assertIn(b"og:image", res.data)
                self.assertIn(b"whatsapp.thumb.jpeg", res.data)

    def test_api_key_protection_and_methods(self):
        from app import app
        with patch("app.table.get_all") as mock_get_all, patch("app.table.update_record") as mock_update:
            mock_get_all.return_value = [{"id": "1", "nombre": "Test"}]
            mock_update.return_value = {"id": "1", "nombre": "Test", "confirmacion": "si"}

            with app.test_client() as client:
                res = client.get("/invitados")
                self.assertEqual(res.status_code, 401)

                res = client.get("/invitados", headers={"X-API-Key": "boda_secret_api_key_2027"})
                self.assertEqual(res.status_code, 200)

                res = client.get("/invitados", headers={"Authorization": "Bearer boda_secret_api_key_2027"})
                self.assertEqual(res.status_code, 200)

                res = client.get("/invitados?api_key=boda_secret_api_key_2027")
                self.assertEqual(res.status_code, 200)

    def test_dynamic_slides_manifest_and_single_slide(self):
        from app import app
        dummy_slides = [
            {"id": "portada", "title": "Portada", "template": "slides/custom.html", "enabled": True, "elements": [{"type": "text", "content": "Celia & Alfredo"}]},
            {"id": "fecha", "title": "Fecha", "template": "slides/custom.html", "enabled": True, "elements": []},
            {"id": "lugar", "title": "Lugar", "template": "slides/custom.html", "enabled": True, "elements": []},
            {"id": "video", "title": "Video", "template": "slides/custom.html", "enabled": True, "elements": []},
            {"id": "itinerario", "title": "Itinerario", "template": "slides/custom.html", "enabled": True, "elements": []},
            {"id": "rsvp", "title": "Confirmación", "template": "slides/custom.html", "enabled": True, "elements": [{"type": "form", "x": 0, "y": 0, "w": 90, "h": 70}]},
        ]
        with patch("app.table.get_by_invitacion") as mock_get_by_inv, patch("app.load_slides", return_value=dummy_slides):
            mock_get_by_inv.return_value = [
                {"id": "abc1", "nombre": "Juan", "apellido": "Perez", "confirmacion": "si", "url": "http://nos.vamos.acas.ar/i/familia_perez_123456"}
            ]
            with app.test_client() as client:
                code = compute_check_code("familia_perez")
                
                res = client.get(f"/i/familia_perez_{code}/slides")
                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertTrue(data["ok"])
                self.assertGreaterEqual(len(data["slides"]), 6)
                self.assertEqual(data["slides"][0]["id"], "portada")

                res_slide = client.get(f"/i/familia_perez_{code}/slide/portada")
                self.assertEqual(res_slide.status_code, 200)
                self.assertIn(b"Celia", res_slide.data)

                res_rsvp = client.get(f"/i/familia_perez_{code}/slide/rsvp")
                self.assertEqual(res_rsvp.status_code, 200)
                self.assertIn(b"storyRsvpForm", res_rsvp.data)

                # Custom slide with form element
                dummy_slides.append({
                    "id": "custom_rsvp",
                    "title": "RSVP Custom",
                    "template": "slides/custom.html",
                    "enabled": True,
                    "elements": [{"type": "form", "x": 5, "y": -10, "w": 85, "h": 65}]
                })
                res_custom_rsvp = client.get(f"/i/familia_perez_{code}/slide/custom_rsvp")
                self.assertEqual(res_custom_rsvp.status_code, 200)
                self.assertIn(b"slide-el-form", res_custom_rsvp.data)
                self.assertIn(b"storyRsvpForm", res_custom_rsvp.data)
                self.assertIn(b"left: calc(50% + 5%);", res_custom_rsvp.data)
                self.assertIn(b"top: calc(50% + -10%);", res_custom_rsvp.data)
                self.assertIn(b"width: 85%;", res_custom_rsvp.data)
                self.assertIn(b"max-height: 65%;", res_custom_rsvp.data)

                res_invalid = client.get("/i/familia_perez_invalid/slides")
                self.assertEqual(res_invalid.status_code, 403)

    def test_panel_route_authentication(self):
        from app import app
        with app.test_client() as client:
            test_slides = [{"id": "portada", "title": "Portada", "background": "sample.jpeg", "enabled": True}]
            auth_headers = get_panel_auth_headers("ari", "gato")

            # 1. Unauthenticated request to /panel returns 401 with WWW-Authenticate header
            res_unauth = client.get("/panel")
            self.assertEqual(res_unauth.status_code, 401)
            self.assertIn("Basic", res_unauth.headers.get("WWW-Authenticate", ""))

            # 2. Invalid credentials return 401
            res_wrong = client.get("/panel", headers=get_panel_auth_headers("ari", "wrongpass"))
            self.assertEqual(res_wrong.status_code, 401)

            res_wrong_user = client.get("/panel", headers=get_panel_auth_headers("wronguser", "gato"))
            self.assertEqual(res_wrong_user.status_code, 401)

            # 3. Valid credentials return 200
            with patch("app.save_slides"), \
                 patch("app.load_slides", return_value=test_slides):
                res = client.get("/panel", headers=auth_headers)
                self.assertEqual(res.status_code, 200)
                self.assertIn(b"Panel de Control", res.data)

                # Slide update with duration and background transforms
                res_slide = client.post("/panel/slide/portada", data={
                    "background": "photo01.jpeg",
                    "duration": 8500,
                    "bg_zoom": 150,
                    "bg_x": 10.5,
                    "bg_y": -15.2
                }, headers=auth_headers)
                self.assertEqual(res_slide.status_code, 200)
                self.assertTrue(res_slide.get_json()["ok"])
                slide_data = res_slide.get_json()["slide"]
                self.assertEqual(slide_data["duration"], 8500)
                self.assertEqual(slide_data["bg_zoom"], 150.0)
                self.assertEqual(slide_data["bg_x"], 10.5)
                self.assertEqual(slide_data["bg_y"], -15.2)

                # Rename background validation
                res_rename = client.post("/panel/rename-background", json={"old_name": "inexistente.jpeg", "new_name": "nuevo.jpeg"}, headers=auth_headers)
                self.assertEqual(res_rename.status_code, 404)

                # Delete background validation
                res_del = client.post("/panel/delete-background", json={"filename": "inexistente.jpeg"}, headers=auth_headers)
                self.assertEqual(res_del.status_code, 404)

                # Desktop background endpoint validation
                res_desktop_404 = client.post("/panel/desktop-background", json={"background": "inexistente.jpeg"}, headers=auth_headers)
                self.assertEqual(res_desktop_404.status_code, 404)

                with patch("os.path.isfile", return_value=True), patch("app.save_styles") as mock_save_styles:
                    res_desktop_ok = client.post("/panel/desktop-background", json={"background": "sample.jpeg"}, headers=auth_headers)
                    self.assertEqual(res_desktop_ok.status_code, 200)
                    self.assertTrue(res_desktop_ok.get_json()["ok"])
                    mock_save_styles.assert_called_once()
                    self.assertEqual(mock_save_styles.call_args[0][0]["desktop_background"], "sample.jpeg")

                # Delete background rejection when in use by a slide
                with patch("app.load_slides", return_value=[{"id": "intro", "title": "Intro", "background": "sample.jpeg"}]):
                    res_in_use_bg = client.post("/panel/delete-background", json={"filename": "sample.jpeg"}, headers=auth_headers)
                    self.assertEqual(res_in_use_bg.status_code, 400)
                    self.assertIn("en uso", res_in_use_bg.get_json()["error"].lower())

                # Delete background rejection when in use as desktop background
                with patch("app.load_slides", return_value=[]), patch("app.load_styles", return_value={"desktop_background": "sample.jpeg"}):
                    res_in_use_desktop = client.post("/panel/delete-background", json={"filename": "sample.jpeg"}, headers=auth_headers)
                    self.assertEqual(res_in_use_desktop.status_code, 400)
                    self.assertIn("en uso", res_in_use_desktop.get_json()["error"].lower())

                # Upload background (empty, single, multiple)
                res_up_empty = client.post("/panel/upload-background", headers=auth_headers)
                self.assertEqual(res_up_empty.status_code, 400)

                import io
                import tempfile
                with tempfile.TemporaryDirectory() as tmp_bg_dir:
                    with patch("app.get_backgrounds_dir", return_value=tmp_bg_dir):
                        f1 = (io.BytesIO(b"fake image 1"), "foto1.jpg")
                        res_up_single = client.post("/panel/upload-background", data={"background_files": [f1]}, headers=auth_headers)
                        self.assertEqual(res_up_single.status_code, 200)
                        self.assertTrue(res_up_single.get_json()["ok"])
                        self.assertEqual(res_up_single.get_json()["count"], 1)

                        f2 = (io.BytesIO(b"fake image 2"), "foto2.png")
                        f3 = (io.BytesIO(b"fake image 3"), "foto3.webp")
                        res_up_multi = client.post("/panel/upload-background", data={"background_files": [f2, f3]}, headers=auth_headers)
                        self.assertEqual(res_up_multi.status_code, 200)
                        self.assertTrue(res_up_multi.get_json()["ok"])
                        self.assertEqual(res_up_multi.get_json()["count"], 2)
                        self.assertIn("foto2.png", res_up_multi.get_json()["filenames"])
                        self.assertIn("foto3.webp", res_up_multi.get_json()["filenames"])

            # 4. Unauthenticated POST endpoints return 401
            res_post_unauth = client.post("/panel/slide/portada", data={"background": "photo01.jpeg"})
            self.assertEqual(res_post_unauth.status_code, 401)

            res_upload_unauth = client.post("/panel/upload-background")
            self.assertEqual(res_upload_unauth.status_code, 401)

            # 5. Dynamic environment variable configuration for custom credentials
            with patch.dict(os.environ, {"PANEL_USER": "custom_admin", "PANEL_PASSWORD": "super_secret"}):
                res_custom_ok = client.get("/panel", headers=get_panel_auth_headers("custom_admin", "super_secret"))
                self.assertEqual(res_custom_ok.status_code, 200)

                res_old_fail = client.get("/panel", headers=get_panel_auth_headers("ari", "gato"))
                self.assertEqual(res_old_fail.status_code, 401)

    def test_slides_freedom_and_modular_custom_templates(self):
        from app import app, load_slides
        with app.test_client() as client:
            # 1. Slides can be deleted normally without special rsvp restrictions
            with patch("app.load_slides", return_value=[{"id": "rsvp", "title": "RSVP"}, {"id": "portada", "title": "Portada"}]), patch("app.save_slides") as mock_save:
                res_del = client.post("/panel/slide/rsvp/delete", headers=get_panel_auth_headers())
                self.assertEqual(res_del.status_code, 200)
                data = res_del.get_json()
                self.assertTrue(data["ok"])
                mock_save.assert_called_once()
                saved_slides = mock_save.call_args[0][0]
                self.assertEqual(len(saved_slides), 1)
                self.assertEqual(saved_slides[0]["id"], "portada")

            # 2. load_slides assigns slides/custom.html to all slides
            custom_list = [{"id": "s1", "title": "S1"}]
            with patch("app.SLIDES_CONFIG_DEFAULT", custom_list), patch("os.path.exists", return_value=False):
                loaded = load_slides()
                self.assertEqual(len(loaded), 1)
                self.assertEqual(loaded[0]["template"], "slides/custom.html")

    def test_resolve_invitation_slide_and_temp_behavior(self):
        from app import app, resolve_invitation_slide
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            bg_dir = os.path.join(tmpdir, "backgrounds")
            temp_dir = os.path.join(bg_dir, "temp")
            os.makedirs(temp_dir, exist_ok=True)

            # Per-slide WebP file (named after slide id 'fecha')
            temp_file = os.path.join(temp_dir, "fecha.webp")
            with open(temp_file, "w") as f:
                f.write("dummy-webp")

            with patch("app.get_backgrounds_dir", return_value=bg_dir):
                # 1. Slide with existing temp webp -> background resolves to temp/{slide_id}.webp and coords reset
                slide_with_temp = {
                    "id": "fecha",
                    "background": "ConGlobo.jpg",
                    "bg_x": -0.9,
                    "bg_y": -3.5,
                    "bg_zoom": 160.0
                }
                resolved = resolve_invitation_slide(slide_with_temp)
                self.assertEqual(resolved["background"], "temp/fecha.webp")
                self.assertEqual(resolved["bg_x"], 0)
                self.assertEqual(resolved["bg_y"], 0)
                self.assertEqual(resolved["bg_zoom"], 100)

                # Original dictionary was not modified
                self.assertEqual(slide_with_temp["background"], "ConGlobo.jpg")
                self.assertEqual(slide_with_temp["bg_zoom"], 160.0)

                # 2. Slide without temp file -> leaves original background and transforms intact
                slide_without_temp = {
                    "id": "portada",
                    "background": "Other.jpg",
                    "bg_x": 5.0,
                    "bg_y": 10.0,
                    "bg_zoom": 120.0
                }
                resolved_no_temp = resolve_invitation_slide(slide_without_temp)
                self.assertEqual(resolved_no_temp["background"], "Other.jpg")
                self.assertEqual(resolved_no_temp["bg_x"], 5.0)
                self.assertEqual(resolved_no_temp["bg_y"], 10.0)
                self.assertEqual(resolved_no_temp["bg_zoom"], 120.0)

                # 3. Video slides are never modified
                for vid in ("clip.mp4", "intro.webm", "sample.mov"):
                    slide_video = {
                        "id": "video",
                        "background": vid,
                        "bg_x": 0,
                        "bg_y": 0,
                        "bg_zoom": 100
                    }
                    resolved_video = resolve_invitation_slide(slide_video)
                    self.assertEqual(resolved_video["background"], vid)

                # 4. Invalidation on slide update in panel removes temp/{slide_id}.webp
                with app.test_client() as client:
                    auth_headers = get_panel_auth_headers("ari", "gato")
                    with patch("app.load_slides", return_value=[dict(slide_with_temp)]), \
                         patch("app.save_slides"):
                        self.assertTrue(os.path.isfile(temp_file))
                        client.post("/panel/slide/fecha", json={"bg_zoom": 150.0}, headers=auth_headers)
                        self.assertFalse(os.path.isfile(temp_file))

                        # Recreate and verify deletion invalidates
                        with open(temp_file, "w") as f:
                            f.write("dummy-webp")
                        self.assertTrue(os.path.isfile(temp_file))
                        client.post("/panel/slide/fecha/delete", headers=auth_headers)
                        self.assertFalse(os.path.isfile(temp_file))

    def test_panel_styles_and_slides_download_upload(self):
        import io
        import json
        import tempfile
        from app import app

        with tempfile.TemporaryDirectory() as tmpdir:
            test_styles_path = os.path.join(tmpdir, "styles.json")
            test_slides_path = os.path.join(tmpdir, "slides.json")

            with patch("app.STYLES_JSON_PATH", test_styles_path), \
                 patch("app.SLIDES_JSON_PATH", test_slides_path):

                with app.test_client() as client:
                    auth_headers = get_panel_auth_headers("ari", "gato")
                    # 1. Unauthenticated protection (401)
                    self.assertEqual(client.get("/panel/styles/download").status_code, 401)
                    self.assertEqual(client.post("/panel/styles/upload").status_code, 401)
                    self.assertEqual(client.get("/panel/slides/download").status_code, 401)
                    self.assertEqual(client.post("/panel/slides/upload").status_code, 401)

                    # 2. Authenticated access
                    # --- STYLES DOWNLOAD ---
                    res_dl_styles = client.get("/panel/styles/download", headers=auth_headers)
                    self.assertEqual(res_dl_styles.status_code, 200)
                    self.assertIn("application/json", res_dl_styles.content_type)
                    self.assertIn("styles.json", res_dl_styles.headers.get("Content-Disposition", ""))
                    res_dl_styles.close()

                    # --- STYLES UPLOAD (JSON payload) ---
                    res_up_styles_json = client.post("/panel/styles/upload", json={
                        "title": {"fontFamily": "Cinzel", "fontSize": 42}
                    }, headers=auth_headers)
                    self.assertEqual(res_up_styles_json.status_code, 200)
                    data_styles = res_up_styles_json.get_json()
                    self.assertTrue(data_styles["ok"])
                    self.assertEqual(data_styles["styles"]["title"]["fontFamily"], "Cinzel")
                    self.assertEqual(data_styles["styles"]["title"]["fontSize"], 42)

                    # --- STYLES UPLOAD (File payload) ---
                    file_styles_data = io.BytesIO(b'{"tag": {"fontFamily": "Pacifico", "fontSize": 18}}')
                    res_up_styles_file = client.post("/panel/styles/upload", data={
                        "file": (file_styles_data, "styles.json")
                    }, headers=auth_headers)
                    self.assertEqual(res_up_styles_file.status_code, 200)
                    data_file_styles = res_up_styles_file.get_json()
                    self.assertTrue(data_file_styles["ok"])
                    self.assertEqual(data_file_styles["styles"]["tag"]["fontFamily"], "Pacifico")

                    # --- STYLES UPLOAD (Invalid data) ---
                    bad_file = io.BytesIO(b'{"invalid_json": ')
                    self.assertEqual(client.post("/panel/styles/upload", data={"file": (bad_file, "styles.json")}, headers=auth_headers).status_code, 400)
                    self.assertEqual(client.post("/panel/styles/upload", json=["not a dict"], headers=auth_headers).status_code, 400)

                    # --- SLIDES DOWNLOAD ---
                    res_dl_slides = client.get("/panel/slides/download", headers=auth_headers)
                    self.assertEqual(res_dl_slides.status_code, 200)
                    self.assertIn("application/json", res_dl_slides.content_type)
                    self.assertIn("slides.json", res_dl_slides.headers.get("Content-Disposition", ""))
                    res_dl_slides.close()

                    # --- SLIDES UPLOAD (JSON payload) ---
                    new_slides = [
                        {"id": "intro", "title": "Intro", "duration": 5000, "elements": []}
                    ]
                    res_up_slides = client.post("/panel/slides/upload", json=new_slides, headers=auth_headers)
                    self.assertEqual(res_up_slides.status_code, 200)
                    data_slides = res_up_slides.get_json()
                    # Check that uploaded slides are saved correctly without forced injection
                    self.assertTrue(any(s["id"] == "intro" for s in data_slides["slides"]))
                    self.assertEqual(len(data_slides["slides"]), 1)

                    # --- SLIDES UPLOAD (File payload) ---
                    file_slides_data = io.BytesIO(json.dumps([{"id": "portada", "title": "Portada", "elements": []}]).encode("utf-8"))
                    res_up_slides_file = client.post("/panel/slides/upload", data={
                        "file": (file_slides_data, "slides.json")
                    }, headers=auth_headers)
                    self.assertEqual(res_up_slides_file.status_code, 200)
                    self.assertTrue(res_up_slides_file.get_json()["ok"])

                    # --- SLIDES UPLOAD (Invalid data) ---
                    self.assertEqual(client.post("/panel/slides/upload", json={"not": "a list"}, headers=auth_headers).status_code, 400)
                    bad_slides_file = io.BytesIO(b'["string instead of object"]')
                    self.assertEqual(client.post("/panel/slides/upload", data={"file": (bad_slides_file, "slides.json")}, headers=auth_headers).status_code, 400)

    def test_panel_fonts_upload_and_delete(self):
        import io
        import tempfile
        from app import app

        with tempfile.TemporaryDirectory() as tmp_fonts_dir:
            with patch("app.get_fonts_dir", return_value=tmp_fonts_dir):
                with app.test_client() as client:
                    auth_headers = get_panel_auth_headers("ari", "gato")
                    # 1. Unauthenticated protection (401)
                    self.assertEqual(client.post("/panel/upload-font").status_code, 401)
                    self.assertEqual(client.post("/panel/delete-font").status_code, 401)

                    # 2. Authenticated access
                    # Missing file
                    res_empty = client.post("/panel/upload-font", headers=auth_headers)
                    self.assertEqual(res_empty.status_code, 400)

                    # Bad extension
                    bad_file = (io.BytesIO(b"fake binary"), "bad.exe")
                    res_bad_ext = client.post("/panel/upload-font", data={"font_file": bad_file}, headers=auth_headers)
                    self.assertEqual(res_bad_ext.status_code, 400)

                    # Successful TTF upload
                    ttf_file = (io.BytesIO(b"dummy ttf content"), "SampleFont.ttf")
                    res_up = client.post("/panel/upload-font", data={"font_file": ttf_file, "font_name": "SampleFont"}, headers=auth_headers)
                    self.assertEqual(res_up.status_code, 200)
                    data_up = res_up.get_json()
                    self.assertTrue(data_up["ok"])
                    self.assertEqual(data_up["filename"], "SampleFont.ttf")
                    self.assertTrue(os.path.exists(os.path.join(tmp_fonts_dir, "SampleFont.ttf")))

                    # Check fonts.css auto-generation
                    css_file = os.path.join(tmp_fonts_dir, "fonts.css")
                    self.assertTrue(os.path.exists(css_file))
                    with open(css_file, "r") as f:
                        css_content = f.read()
                    self.assertIn("font-family: 'SampleFont'", css_content)

                    # Delete: Path traversal protection
                    res_trav = client.post("/panel/delete-font", json={"filename": "../evil.ttf"}, headers=auth_headers)
                    self.assertEqual(res_trav.status_code, 400)

                    # Delete: Not found
                    res_nf = client.post("/panel/delete-font", json={"filename": "NonExistent.ttf"}, headers=auth_headers)
                    self.assertEqual(res_nf.status_code, 404)

                    # Delete: In-use protection
                    with patch("app.is_font_in_use", return_value=True):
                        res_in_use = client.post("/panel/delete-font", json={"filename": "SampleFont.ttf"}, headers=auth_headers)
                        self.assertEqual(res_in_use.status_code, 400)
                        self.assertIn("en uso", res_in_use.get_json()["error"].lower())

                    # Delete: Aveny-T system required sticker font protection
                    aveny_file = os.path.join(tmp_fonts_dir, "aveny-t-medium.ttf")
                    with open(aveny_file, "wb") as f:
                        f.write(b"font-data")
                    res_aveny = client.post("/panel/delete-font", json={"filename": "aveny-t-medium.ttf"}, headers=auth_headers)
                    self.assertEqual(res_aveny.status_code, 400)
                    self.assertIn("en uso", res_aveny.get_json()["error"].lower())
                    self.assertTrue(os.path.exists(aveny_file))

                    # Successful delete
                    res_del = client.post("/panel/delete-font", json={"filename": "SampleFont.ttf"}, headers=auth_headers)
                    self.assertEqual(res_del.status_code, 200)
                    self.assertTrue(res_del.get_json()["ok"])
                    self.assertFalse(os.path.exists(os.path.join(tmp_fonts_dir, "SampleFont.ttf")))

    def test_guest_names_formatting_and_escape_variable(self):
        from app import app, format_group_names, replace_guest_names, format_currency_ar

        # 1. Grammar joining in Spanish
        self.assertEqual(format_group_names(["Alfredo"]), "Alfredo")
        self.assertEqual(format_group_names(["Alfredo", "Celia"]), "Alfredo y Celia")
        self.assertEqual(format_group_names(["Alfredo", "Celia", "Juan"]), "Alfredo, Celia y Juan")
        self.assertEqual(format_group_names(["Alfredo", "Celia", "Juan", "Pedro"]), "Alfredo, Celia, Juan y Pedro")
        self.assertEqual(format_group_names([], fallback="Familia / Pareja"), "Familia / Pareja")
        self.assertEqual(format_group_names(["", "   "], fallback="Invitados"), "Invitados")

        # 2. Escape sequence replacement ({name}, {nombre}, {nombres}, {NAME})
        self.assertEqual(replace_guest_names("¡Hola {name}!", "Juan"), "¡Hola Juan!")
        self.assertEqual(replace_guest_names("Bienvenidos {nombre}", "Juan y María"), "Bienvenidos Juan y María")
        self.assertEqual(replace_guest_names("Para {NOMBRES}", "Juan, María y Pedro"), "Para Juan, María y Pedro")
        self.assertEqual(replace_guest_names("Mesa de {NAME}", "Celia"), "Mesa de Celia")

        # 2b. Price / Value variable replacement ({value}, {valor}, {monto}, {precio})
        self.assertEqual(format_currency_ar("90000"), "$90.000")
        self.assertEqual(format_currency_ar("45.000"), "$45.000")
        self.assertEqual(format_currency_ar(""), "")
        self.assertEqual(replace_guest_names("Tarjeta: {value}", card_value="$105.000"), "Tarjeta: $105.000")
        self.assertEqual(replace_guest_names("Tarjeta: ${value}", card_value="$105.000"), "Tarjeta: $105.000")
        self.assertEqual(replace_guest_names("Tarjeta: {value}", card_value="$90.000"), "Tarjeta: $90.000")
        self.assertEqual(replace_guest_names("Tarjeta: ${value}", card_value="$90.000"), "Tarjeta: $90.000")
        self.assertEqual(replace_guest_names("Valor: {valor}", card_value="$45.000"), "Valor: $45.000")
        self.assertEqual(replace_guest_names("Costo: {PRECIO}", card_value="$45.000"), "Costo: $45.000")
        self.assertEqual(replace_guest_names("Monto: {monto}", card_value="$45.000"), "Monto: $45.000")
        self.assertEqual(replace_guest_names("Hola {name}, tarjeta {value}", "Juan", card_value="$105.000"), "Hola Juan, tarjeta $105.000")

        # 3. Singular vs Plural conditional pattern (a|b)
        self.assertEqual(replace_guest_names("(Te|Los) esperamos {name}", "Juan", count=1), "Te esperamos Juan")
        self.assertEqual(replace_guest_names("(Te|Los) esperamos {name}", "Juan y María", count=2), "Los esperamos Juan y María")
        self.assertEqual(replace_guest_names("¡Bienvenido(|s)!", "Juan", count=1), "¡Bienvenido!")
        self.assertEqual(replace_guest_names("¡Bienvenido(|s)!", "Juan y María", count=2), "¡Bienvenidos!")
        self.assertEqual(replace_guest_names("(Te|Los) esperamos (Estacionamiento)", "Juan", count=1), "Te esperamos (Estacionamiento)")
        self.assertEqual(replace_guest_names("(Te|Los) esperamos (Estacionamiento)", "Juan y María", count=2), "Los esperamos (Estacionamiento)")
        # Inferred from group_names when count is None
        self.assertEqual(replace_guest_names("(Te|Los) queremos", "Pedro"), "Te queremos")
        self.assertEqual(replace_guest_names("(Te|Los) queremos", "Pedro y Ana"), "Los queremos")
        # Also supports {singular|plural} format
        self.assertEqual(replace_guest_names("{Te|Los} esperamos", "Pedro", count=1), "Te esperamos")
        self.assertEqual(replace_guest_names("{Te|Los} esperamos", "Pedro y Ana", count=2), "Los esperamos")

        # 4. SSR Slide rendering replacing {name} and (a|b)
        with app.test_client() as client:
            code = parse_and_validate_token._cache_token = compute_check_code("familia_gomez")
            dummy_guests = [
                {"id": "g1", "invitacion_id": "familia_gomez", "nombre": "Juan", "apellido": "Gómez"},
                {"id": "g2", "invitacion_id": "familia_gomez", "nombre": "María", "apellido": "López"},
            ]
            dummy_slides = [{
                "id": "slide_saludo",
                "title": "Saludo",
                "template": "slides/custom.html",
                "enabled": True,
                "elements": [
                    {"type": "text", "style": "title", "content": "(Te|Los) esperamos {name}!"},
                    {"type": "location", "content": "Mesa de {nombre}", "url": "https://maps.google.com"},
                    {"type": "copy", "icon": "bank", "content": "Alias: BODA.CELIA.ALFREDO", "copy_val": "BODA.CELIA.ALFREDO"}
                ]
            }]
            with patch("app.table.get_by_invitacion", return_value=dummy_guests), \
                 patch("app.load_slides", return_value=dummy_slides):
                res = client.get(f"/i/familia_gomez_{code}/slide/slide_saludo")
                self.assertEqual(res.status_code, 200)
                self.assertIn(b"data-raw-content=\"(Te|Los) esperamos {name}!\"", res.data)
                self.assertIn(b"Los esperamos Juan y Mar\xc3\xada!", res.data)
                self.assertIn(b"Mesa de Juan y Mar\xc3\xada", res.data)
                self.assertIn(b"instagram-copy-sticker", res.data)
                self.assertIn(b"data-copy-val=\"BODA.CELIA.ALFREDO\"", res.data)
                self.assertIn(b"bank-icon", res.data)

            # Singular guest check
            single_guest = [{"id": "g1", "invitacion_id": "familia_gomez", "nombre": "Juan", "apellido": "Gómez"}]
            with patch("app.table.get_by_invitacion", return_value=single_guest), \
                 patch("app.load_slides", return_value=dummy_slides):
                res = client.get(f"/i/familia_gomez_{code}/slide/slide_saludo")
                self.assertEqual(res.status_code, 200)
                self.assertIn(b"Te esperamos Juan!", res.data)

            # Curly braces format {te|los} check and data-names-count attribute on index.html
            curly_slides = [{
                "id": "slide_curly",
                "title": "Curly",
                "template": "slides/custom.html",
                "enabled": True,
                "elements": [
                    {"type": "text", "style": "title", "content": "{Te|Los} invitamos {name}"}
                ]
            }]
            with patch("app.table.get_by_invitacion", return_value=single_guest), \
                 patch("app.load_slides", return_value=curly_slides):
                res_main = client.get(f"/i/familia_gomez_{code}")
                self.assertEqual(res_main.status_code, 200)
                self.assertIn(b"data-names-count=\"1\"", res_main.data)
                self.assertIn(b"Te invitamos Juan", res_main.data)

            with patch("app.table.get_by_invitacion", return_value=dummy_guests), \
                 patch("app.load_slides", return_value=curly_slides):
                res_plural = client.get(f"/i/familia_gomez_{code}")
                self.assertEqual(res_plural.status_code, 200)
                self.assertIn(b"data-names-count=\"2\"", res_plural.data)
                self.assertIn(b"Los invitamos Juan y Mar\xc3\xada", res_plural.data)

        # 4. Backend safeguard: do not allow clearing the sole remaining name
        with app.test_client() as client:
            sole_guest = {"id": "g_sole", "invitacion_id": "inv_sole", "nombre": "Carlos"}
            with patch("app.table.get_by_id", return_value=sole_guest), \
                 patch("app.table.get_by_invitacion", return_value=[sole_guest]), \
                 patch("app.table.update_record", return_value=sole_guest) as mock_update:
                res = client.put("/invitados/g_sole", json={"nombre": "   "})
                self.assertEqual(res.status_code, 200)
                # The name should NOT have been cleared to empty, but preserved as 'Carlos'
                called_data = mock_update.call_args[0][1]
                self.assertEqual(called_data["nombre"], "Carlos")

    def test_itinerary_two_column_formatting(self):
        from app import app, format_slide_text, replace_guest_names

        # 1. Base edge cases
        self.assertEqual(format_slide_text(""), "")
        self.assertEqual(format_slide_text(None), "")

        # 2. Plain text without pipes is preserved and HTML-escaped
        self.assertEqual(format_slide_text("Nos casamos"), "Nos casamos")
        self.assertEqual(format_slide_text("Línea 1\nLínea 2"), "Línea 1\nLínea 2")
        self.assertEqual(format_slide_text("Texto con <script>alert(1)</script>"), "Texto con &lt;script&gt;alert(1)&lt;/script&gt;")

        # 3. Simple 2-column itinerary with pipes
        itinerary_raw = "18:00 | Recepción\n18:30 | Ceremonia civil\n19:30 | Cena"
        res = format_slide_text(itinerary_raw)
        self.assertIn('<div class="slide-itinerary-grid">', res)
        self.assertIn('<span class="itinerary-col-time">18:00</span>', res)
        self.assertIn('<span class="itinerary-col-event">Recepción</span>', res)
        self.assertIn('<span class="itinerary-col-time">18:30</span>', res)
        self.assertIn('<span class="itinerary-col-event">Ceremonia civil</span>', res)
        self.assertIn('<span class="itinerary-col-time">19:30</span>', res)
        self.assertIn('<span class="itinerary-col-event">Cena</span>', res)

        # 4. Mixed headers and empty line spacers
        mixed_raw = "CRONOGRAMA\n18:00 | Recepción\n\n02:00 | Fin de fiesta"
        mixed_res = format_slide_text(mixed_raw)
        self.assertIn('<div class="itinerary-full-row">CRONOGRAMA</div>', mixed_res)
        self.assertIn('<div class="itinerary-spacer"></div>', mixed_res)
        self.assertIn('<span class="itinerary-col-time">02:00</span>', mixed_res)
        self.assertIn('<span class="itinerary-col-event">Fin de fiesta</span>', mixed_res)

        # 5. HTML escaping in both time and event columns (anti-XSS)
        xss_raw = "<b>18:00</b> | <img src=x onerror=alert(1)>"
        xss_res = format_slide_text(xss_raw)
        self.assertIn("&lt;b&gt;18:00&lt;/b&gt;", xss_res)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", xss_res)
        self.assertNotIn("<script>", xss_res)

        # 6. Combined with replace_guest_names and SSR slide view
        slide_text = "18:00 | Recepción de {name}\n18:30 | {Te|Los} esperamos"
        replaced = replace_guest_names(slide_text, "Juan y María", count=2)
        formatted = format_slide_text(replaced)
        self.assertIn("Recepción de Juan y María", formatted)
        self.assertIn("Los esperamos", formatted)
        self.assertIn('<div class="slide-itinerary-grid">', formatted)

        # 7. SSR rendering in /slide/<slide_id> endpoint
        code = compute_check_code("familia_gomez")
        dummy_guests = [{"id": "g1", "invitacion_id": "familia_gomez", "nombre": "Juan", "apellido": "Gómez"}]
        itinerary_slides = [{
            "id": "slide_itinerario",
            "title": "Itinerario",
            "template": "slides/custom.html",
            "enabled": True,
            "elements": [
                {
                    "type": "text",
                    "style": "body",
                    "content": "18:00 | Recepción\n18:30 | Ceremonia civil\n19:30 | Cena\n21:00 | Baile\n02:00 | Fin"
                }
            ]
        }]
        with app.test_client() as client:
            with patch("app.table.get_by_invitacion", return_value=dummy_guests), \
                 patch("app.load_slides", return_value=itinerary_slides):
                res_slide = client.get(f"/i/familia_gomez_{code}/slide/slide_itinerario")
                self.assertEqual(res_slide.status_code, 200)
                self.assertIn(b"slide-itinerary-grid", res_slide.data)
                self.assertIn(b"itinerary-col-time", res_slide.data)
                self.assertIn(b"18:00", res_slide.data)
                self.assertIn(b"Recepci\xc3\xb3n", res_slide.data)
                self.assertIn(b"Ceremonia civil", res_slide.data)

    def test_slide_id_editing_and_hidden_parameter(self):
        from app import app
        with app.test_client() as client:
            with patch("app.is_dev_mode", return_value=True), patch("app.get_backgrounds_dir", return_value="/tmp/test_bg_dir"):
                existing_slides = [
                    {"id": "portada", "template": "slides/custom.html", "duration": 4000, "hidden": False},
                    {"id": "fecha", "template": "slides/custom.html", "duration": 5000, "hidden": False},
                    {"id": "triste", "template": "slides/custom.html", "duration": 0, "hidden": True}
                ]
                with patch("app.load_slides", return_value=existing_slides), patch("app.save_slides") as mock_save:
                    # 1. Update slide ID successfully
                    res_up = client.post("/panel/slide/portada", json={"id": "bienvenida", "hidden": True}, headers=get_panel_auth_headers())
                    self.assertEqual(res_up.status_code, 200)
                    saved_data = mock_save.call_args[0][0]
                    target = next(s for s in saved_data if s["id"] == "bienvenida")
                    self.assertTrue(target["hidden"])

                    # 2. Prevent collision with another existing slide ID
                    res_coll = client.post("/panel/slide/fecha", json={"id": "triste"}, headers=get_panel_auth_headers())
                    self.assertEqual(res_coll.status_code, 400)
                    self.assertIn("en uso", res_coll.get_json()["error"])

            # 3. Hidden slide SSR rendering: display: none when not all rejected, visible when all rejected
            code = parse_and_validate_token._cache_token = compute_check_code("familia_test")
            attending_guests = [{"id": "g1", "invitacion_id": "familia_test", "confirmacion": "si"}]
            rejected_guests = [{"id": "g1", "invitacion_id": "familia_test", "confirmacion": "no"}]
            slides_with_hidden = [{"id": "gato", "template": "slides/custom.html", "hidden": True, "elements": []}]

            with patch("app.table.get_by_invitacion", return_value=attending_guests), \
                 patch("app.load_slides", return_value=slides_with_hidden):
                res_attending = client.get(f"/i/familia_test_{code}/slide/gato")
                self.assertEqual(res_attending.status_code, 200)
                self.assertIn(b"data-hidden=\"true\"", res_attending.data)
                self.assertIn(b"display: none;", res_attending.data)

            with patch("app.table.get_by_invitacion", return_value=rejected_guests), \
                 patch("app.load_slides", return_value=slides_with_hidden):
                res_rejected = client.get(f"/i/familia_test_{code}/slide/gato")
                self.assertEqual(res_rejected.status_code, 200)
                self.assertIn(b"data-hidden=\"true\"", res_rejected.data)
                self.assertNotIn(b"display: none;", res_rejected.data)

    def test_parse_currency_amount(self):
        self.assertEqual(parse_currency_amount("50000"), 50000.0)
        self.assertEqual(parse_currency_amount("$ 50.000"), 50000.0)
        self.assertEqual(parse_currency_amount("$ 50.000,50"), 50000.5)
        self.assertEqual(parse_currency_amount("$45,50"), 45.5)
        self.assertEqual(parse_currency_amount(""), 0.0)
        self.assertEqual(parse_currency_amount(None), 0.0)

    def test_upload_comprobante_and_propagation(self):
        import io
        from app import app
        with app.test_client() as client:
            code = compute_check_code("familia_pago")
            token = f"familia_pago_{code}"
            dummy_guests = [
                {"id": "g1", "invitacion_id": "familia_pago", "nombre": "Juan"},
                {"id": "g2", "invitacion_id": "familia_pago", "nombre": "Maria"},
            ]
            with patch("app.table.get_by_invitacion", return_value=dummy_guests), \
                 patch("app.table.update_record") as mock_update:
                data = {"file": (io.BytesIO(b"%PDF-1.4 mock content"), "comprobante.pdf")}
                res = client.post(f"/i/{token}/comprobante", data=data, content_type="multipart/form-data")
                self.assertEqual(res.status_code, 200)
                body = res.get_json()
                self.assertTrue(body["ok"])
                self.assertIn("/comprobantes/", body["url"])
                self.assertTrue(body["url"].endswith(".pdf"))
                # Verify update was called for BOTH guests of that invitation
                self.assertEqual(mock_update.call_count, 2)
                called_ids = [call[0][0] for call in mock_update.call_args_list]
                self.assertIn("g1", called_ids)
                self.assertIn("g2", called_ids)

    def test_upload_comprobante_disallowed_extension(self):
        import io
        from app import app
        with app.test_client() as client:
            code = compute_check_code("familia_pago")
            token = f"familia_pago_{code}"
            data = {"file": (io.BytesIO(b"malicious script"), "hack.exe")}
            res = client.post(f"/i/{token}/comprobante", data=data, content_type="multipart/form-data")
            self.assertEqual(res.status_code, 400)
            self.assertFalse(res.get_json()["ok"])
            self.assertIn("Formato no permitido", res.get_json()["error"])

    def test_delete_comprobante(self):
        from app import app
        with app.test_client() as client:
            code = compute_check_code("familia_pago")
            token = f"familia_pago_{code}"
            dummy_guests = [
                {"id": "g1", "invitacion_id": "familia_pago", "comprobante": "http://test/file.pdf"},
            ]
            with patch("app.table.get_by_invitacion", return_value=dummy_guests), \
                 patch("app.table.update_record") as mock_update:
                res = client.delete(f"/i/{token}/comprobante")
                self.assertEqual(res.status_code, 200)
                self.assertTrue(res.get_json()["ok"])
                mock_update.assert_called_once_with("g1", {"comprobante": ""})

    def test_flush_invitacion_cache_endpoint(self):
        from app import app
        with app.test_client() as client:
            code = compute_check_code("familia_flush")
            token = f"familia_flush_{code}"
            with patch("app.table.flush_local_to_remote", return_value=3) as mock_flush:
                # 1. Valid invitation token
                res = client.post(f"/i/{token}/flush")
                self.assertEqual(res.status_code, 200)
                body = res.get_json()
                self.assertTrue(body["ok"])
                self.assertEqual(body["flushed"], 3)
                mock_flush.assert_called_once()

                # 2. Invalid token
                res_invalid = client.post("/i/familia_flush_wrongcode/flush")
                self.assertEqual(res_invalid.status_code, 403)
                self.assertFalse(res_invalid.get_json()["ok"])

    def test_payment_alias_cbu_rendering(self):
        from app import app
        with app.test_client() as client:
            code = compute_check_code("familia_alias")
            token = f"familia_alias_{code}"
            dummy_guests = [
                {"id": "g1", "invitacion_id": "familia_alias", "nombre": "Juan", "apellido": "Perez", "montotarjeta": "50000"}
            ]
            dummy_slides = [{
                "id": "slide_rsvp",
                "template": "slides/custom.html",
                "enabled": True,
                "elements": [
                    {"type": "form", "alias_cbu": "ALIAS.TEST.BODA"}
                ]
            }]
            with patch("app.table.get_by_invitacion", return_value=dummy_guests), \
                 patch("app.load_slides", return_value=dummy_slides):
                res = client.get(f"/i/{token}")
                self.assertEqual(res.status_code, 200)
                self.assertIn(b"payment-alias-badge", res.data)
                self.assertIn(b"data-copy-val=\"ALIAS.TEST.BODA\"", res.data)
                self.assertIn(b"ALIAS.TEST.BODA", res.data)
                self.assertNotIn(b"rsvp-payment-icon", res.data)

    def test_telegram_wedding_stats_tarjetas(self):
        import telegram_bot

        mock_tbl = MagicMock()
        mock_tbl.get_all.return_value = [
            # Tarjeta 1: Complete YES (2 guests)
            {"id": "g1", "invitacion_id": "fam_si", "dequien": "Celia Amigos", "confirmacion": "si", "pagado": "si"},
            {"id": "g2", "invitacion_id": "fam_si", "dequien": "Celia Amigos", "confirmacion": "si", "pagado": ""},
            # Tarjeta 2: Complete NO (2 guests)
            {"id": "g3", "invitacion_id": "fam_no", "dequien": "Alfredo Familia", "confirmacion": "no", "pagado": ""},
            {"id": "g4", "invitacion_id": "fam_no", "dequien": "Alfredo Familia", "confirmacion": "rechazado", "pagado": ""},
            # Tarjeta 3: Partial mixed yes/no (2 guests)
            {"id": "g5", "invitacion_id": "fam_mix", "dequien": "Celia Familia", "confirmacion": "si", "pagado": "si"},
            {"id": "g6", "invitacion_id": "fam_mix", "dequien": "Celia Familia", "confirmacion": "no", "pagado": ""},
            # Tarjeta 4: Partial mixed yes/pending (2 guests)
            {"id": "g7", "invitacion_id": "fam_pend_mix", "dequien": "Alfredo Amigos", "confirmacion": "si", "pagado": ""},
            {"id": "g8", "invitacion_id": "fam_pend_mix", "dequien": "Alfredo Amigos", "confirmacion": "", "pagado": ""},
            # Tarjeta 5: Full pending (2 guests)
            {"id": "g9", "invitacion_id": "fam_pend", "dequien": "Celia Oficina", "confirmacion": "", "pagado": ""},
            {"id": "g10", "invitacion_id": "fam_pend", "dequien": "Celia Oficina", "confirmacion": "", "pagado": ""},
            # Tarjeta 6: Single guest Complete NO (fallback to id)
            {"id": "g11", "confirmacion": "no", "pagado": ""},
        ]

        stats = telegram_bot._get_wedding_stats(mock_tbl)

        # Check Individual counts
        self.assertIn("Total: <b>11</b>", stats)
        self.assertIn("Confirmados: <b>4</b>", stats)
        self.assertIn("No asisten: <b>4</b>", stats)
        self.assertIn("Pendientes: <b>3</b>", stats)

        # Check Por origen (de quien) counts
        self.assertIn("👥 <b>Por origen (de quién):</b>", stats)
        self.assertIn("• <b>Celia:</b> <b>6</b> (✅ 3 · ❌ 1 · ⏳ 2 · 💳 2)", stats)
        self.assertIn("• <b>Alfredo:</b> <b>4</b> (✅ 1 · ❌ 2 · ⏳ 1 · 💳 0)", stats)
        self.assertIn("• <b>Sin definir:</b> <b>1</b> (✅ 0 · ❌ 1 · ⏳ 0 · 💳 0)", stats)

        # Check Tarjeta counts
        self.assertIn("Total tarjetas: <b>6</b>", stats)
        self.assertIn("• 📖 Abiertas: <b>0</b>", stats)
        self.assertIn("Completas confirmadas: <b>1</b>", stats)
        self.assertIn("Tarjetas completas que no asisten: <b>2</b>", stats)
        self.assertIn("Tarjetas con confirmación parcial: <b>2</b>", stats)
        self.assertIn("Sin responder: <b>1</b>", stats)

        # Check payment section
        self.assertIn("💳 <b>Pagos:</b>", stats)
        self.assertIn("• ✅ Grupos completos pagados: <b>1</b>", stats)
        self.assertIn("• ⚠️ Invitaciones con pago parcial: <b>1</b>", stats)
        self.assertIn("• 🎫 Invitaciones individuales pagadas: <b>2</b>", stats)

    def test_invitacion_id_change_auto_updates_url_and_whatsapp(self):
        # 1. Sheet has a record where invitacion_id was changed to 'familia_nueva',
        # but the url and whatsapp cells still hold the old URL/WA for 'familia_vieja'
        old_code = compute_check_code("familia_vieja")
        old_url = f"{BASE_URL}/i/familia_vieja_{old_code}"
        old_wa = f"https://wa.me/5491112345678?text={old_url}"

        sample_data = [
            {
                "id": "rec001",
                "invitacion_id": "familia_nueva",
                "telefono": "+54 9 11 1234-5678",
                "url": old_url,
                "whatsapp": old_wa,
                "nombre": "Carlos",
                "apellido": "Perez",
            }
        ]
        self.mock_ws.get_all_records.return_value = sample_data
        self.mock_ws.batch_update.reset_mock()

        records = self.table.get_all(force_remote=True)
        new_code = compute_check_code("familia_nueva")
        expected_url = f"{BASE_URL}/familia_nueva_{new_code}"

        # URL and WhatsApp must be automatically updated to match familia_nueva
        self.assertEqual(records[0]["url"], expected_url)
        self.assertIn("familia_nueva", records[0]["whatsapp"])
        self.assertIn(new_code, records[0]["whatsapp"])

        # And batch_update must have been dispatched to Google Sheets to repair the row
        self.assertEqual(self.mock_ws.batch_update.call_count, 1)

        # 2. Local update_record changing invitacion_id also regenerates url and whatsapp
        updated = self.table.update_record("rec001", {"invitacion_id": "amigos_futbol"})
        futbol_code = compute_check_code("amigos_futbol")
        expected_futbol_url = f"{BASE_URL}/amigos_futbol_{futbol_code}"

        self.assertEqual(updated["url"], expected_futbol_url)
        self.assertIn("amigos_futbol", updated["whatsapp"])
        self.assertIn(futbol_code, updated["whatsapp"])

    def test_mark_opened_human_and_crawler(self):
        sample_data = [
            {"id": "op1", "invitacion_id": "familia_abierta", "nombre": "Juan", "apellido": "Perez", "abierto": ""},
            {"id": "op2", "invitacion_id": "familia_abierta", "nombre": "Maria", "apellido": "Perez", "abierto": ""}
        ]
        self.mock_ws.get_all_records.return_value = sample_data
        self.table.get_all(force_remote=True)

        # 1. Mark opened for the first time
        updated = self.table.mark_opened("familia_abierta")
        self.assertTrue(updated)

        cache = self.table._load_cache()
        matched = [r for r in cache["records"] if r.get("invitacion_id") == "familia_abierta"]
        self.assertEqual(len(matched), 2)
        for r in matched:
            self.assertEqual(r.get("abierto"), "si")
            self.assertEqual(r.get("_sync_state"), "LOCAL")

        # 2. Subsequent call does not mark again (returns False)
        updated_again = self.table.mark_opened("familia_abierta")
        self.assertFalse(updated_again)

    def test_index_page_marks_opened_for_human_not_crawler(self):
        from app import app
        with patch("app.table.get_by_invitacion") as mock_get_by_inv, \
             patch("app.table.mark_opened") as mock_mark_opened:
            mock_get_by_inv.return_value = [
                {"id": "abc1", "nombre": "Juan", "apellido": "Perez", "confirmacion": "si", "url": "http://nos.vamos.acas.ar/i/familia_perez_123456"}
            ]
            code = compute_check_code("familia_perez")

            with app.test_client() as client:
                # Crawler request (WhatsApp) -> must NOT mark opened
                res_crawler = client.get(f"/i/familia_perez_{code}", headers={"User-Agent": "WhatsApp/2.21.12.21 A"})
                self.assertEqual(res_crawler.status_code, 200)
                mock_mark_opened.assert_not_called()

                # Crawler request (Googlebot) -> must NOT mark opened
                res_bot = client.get(f"/i/familia_perez_{code}", headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"})
                self.assertEqual(res_bot.status_code, 200)
                mock_mark_opened.assert_not_called()

                # Human browser request -> MUST mark opened
                res_human = client.get(f"/i/familia_perez_{code}", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"})
                self.assertEqual(res_human.status_code, 200)
                mock_mark_opened.assert_called_once_with("familia_perez")

    def test_default_reserva_monto_assignment(self):
        # 1. Reserva without monto -> defaults to DEFAULT_RESERVA_MONTO
        item1, _ = self.table._map_record_to_row({"nombre": "Lucas", "nivel": "Reserva", "montotarjeta": ""}, "2026-10-18")
        self.assertEqual(item1["montotarjeta"], DEFAULT_RESERVA_MONTO)

        # 2. Reserva with monto "0" -> defaults to DEFAULT_RESERVA_MONTO
        item2, _ = self.table._map_record_to_row({"nombre": "Martin", "nivel": "reserva", "montotarjeta": "0"}, "2026-10-18")
        self.assertEqual(item2["montotarjeta"], DEFAULT_RESERVA_MONTO)

        # 3. Reserva with existing custom monto -> keeps existing custom monto
        item3, _ = self.table._map_record_to_row({"nombre": "Sofia", "nivel": "Reserva", "montotarjeta": "50000"}, "2026-10-18")
        self.assertEqual(item3["montotarjeta"], "50000")

        # 4. Invitado without monto -> keeps empty monto
        item4, _ = self.table._map_record_to_row({"nombre": "Clara", "nivel": "Invitado", "montotarjeta": ""}, "2026-10-18")
        self.assertEqual(item4["montotarjeta"], "")

    def test_get_group_target_type(self):
        # 1. At least one guest has nivel="Reserva" -> "reserva"
        g_res = [
            {"id": "1", "nivel": "Invitado", "montotarjeta": ""},
            {"id": "2", "nivel": "Reserva", "montotarjeta": "100000"},
        ]
        self.assertEqual(get_group_target_type(g_res), "reserva")

        # 2. At least one guest has montotarjeta > 0 even if nivel is empty -> "reserva"
        g_monto = [
            {"id": "1", "nivel": "", "montotarjeta": "50000"},
            {"id": "2", "nivel": "", "montotarjeta": ""},
        ]
        self.assertEqual(get_group_target_type(g_monto), "reserva")

        # 3. All guests are Invitado with 0 or empty monto -> "invitacion"
        g_inv = [
            {"id": "1", "nivel": "Invitado", "montotarjeta": ""},
            {"id": "2", "nivel": "Invitado", "montotarjeta": "0"},
        ]
        self.assertEqual(get_group_target_type(g_inv), "invitacion")

    def test_active_slides_audience_filtering(self):
        from app import get_guest_context
        test_slides = [
            {"id": "intro", "target": "all", "enabled": True},
            {"id": "reservas", "target": "reserva", "enabled": True},
            {"id": "invitacion", "target": "invitacion", "enabled": True},
        ]

        with patch("app.load_slides", return_value=test_slides):
            # Test group with Reserva: must have intro and reservas, NOT invitacion
            with patch("app.table.get_by_invitacion", return_value=[{"id": "1", "nivel": "Reserva", "montotarjeta": "100000"}]):
                ctx_reserva = get_guest_context("slug_reserva")
                self.assertEqual(ctx_reserva["group_type"], "reserva")
                slide_ids = [s["id"] for s in ctx_reserva["active_slides"]]
                self.assertIn("intro", slide_ids)
                self.assertIn("reservas", slide_ids)
                self.assertNotIn("invitacion", slide_ids)

            # Test group with Invitacion: must have intro and invitacion, NOT reservas
            with patch("app.table.get_by_invitacion", return_value=[{"id": "2", "nivel": "Invitado", "montotarjeta": ""}]):
                ctx_invitacion = get_guest_context("slug_invitacion")
                self.assertEqual(ctx_invitacion["group_type"], "invitacion")
                slide_ids = [s["id"] for s in ctx_invitacion["active_slides"]]
                self.assertIn("intro", slide_ids)
                self.assertIn("invitacion", slide_ids)
                self.assertNotIn("reservas", slide_ids)


    def test_clean_token_route_without_i(self):
        code = compute_check_code("familia_clean")
        token = f"familia_clean_{code}"
        sample_data = [
            {"id": "c1", "nombre": "Ana", "apellido": "Lopez", "confirmacion": "si", "invitacion_id": "familia_clean"}
        ]
        from app import app
        with patch.object(self.table, "get_all", return_value=sample_data):
            with patch("app.table", self.table):
                with app.test_client() as client:
                    # 1. Clean route /<token> (200 OK)
                    res_clean = client.get(f"/{token}")
                    self.assertEqual(res_clean.status_code, 200)

                    # 2. Legacy route /i/<token> (200 OK backward compatibility)
                    res_legacy = client.get(f"/i/{token}")
                    self.assertEqual(res_legacy.status_code, 200)

                    # 3. Clean subroute /<token>/slides (200 OK)
                    res_slides = client.get(f"/{token}/slides")
                    self.assertEqual(res_slides.status_code, 200)
                    self.assertTrue(res_slides.json["ok"])

                    # 4. Reserved root path /panel is not captured as token
                    res_panel = client.get("/panel")
                    self.assertNotEqual(res_panel.status_code, 403)


if __name__ == "__main__":
    unittest.main()

