import os
import time
import unittest
from unittest.mock import MagicMock, patch
from sheets import (
    GoogleSheetsTable,
    FIELDNAMES,
    BASE_URL,
    compute_check_code,
    generate_invitation_url,
    parse_and_validate_token,
    clean_phone_number,
    generate_whatsapp_url,
)


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

        # Querying the new group causes a cache miss, which immediately triggers a remote sync from Google Sheets
        found = self.table.get_by_invitacion("celia_y_alfredo")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["nombre"], "Celia")
        self.assertEqual(self.mock_ws.get_all_records.call_count, 2)


    def test_add_records(self):
        self.mock_ws.get_all_records.return_value = []
        new_record = {"apellido": "Lopez", "nombre": "Carlos", "telefono": "+54 9 11 1234-5678", "asistencia": "si", "alimentacion": ["celiaco"], "invitacion_id": "amigos"}
        res = self.table.add_records([new_record])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["apellido"], "Lopez")
        self.assertEqual(res[0]["pa_celiaco"], "si")
        self.assertEqual(len(res[0]["id"]), 8)
        self.assertTrue(res[0]["url"].startswith(f"{BASE_URL}/i/amigos_"))
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
        self.assertTrue(records[0]["url"].startswith(f"{BASE_URL}/i/familia_perez_"))
        self.assertTrue(records[0]["whatsapp"].startswith("https://wa.me/5491198765432?text="))

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
        self.assertEqual(url, f"{base_url}/i/familia_rodriguez_{code}")

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
            {"id": "rsvp", "title": "Confirmación", "template": "slides/rsvp.html", "enabled": True, "elements": []},
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

    def test_panel_route_dev_mode_and_production_protection(self):
        from app import app
        with app.test_client() as client:
            # 1. Dev mode active -> 200 OK (isolate save_slides so tests don't overwrite user slides.json)
            with patch("app.is_dev_mode", return_value=True), patch("app.save_slides"):
                res = client.get("/panel")
                self.assertEqual(res.status_code, 200)
                self.assertIn(b"Panel de Control", res.data)

                # Slide update with duration and background transforms in dev mode
                res_slide = client.post("/panel/slide/portada", data={
                    "background": "photo01.jpeg",
                    "duration": 8500,
                    "bg_zoom": 150,
                    "bg_x": 10.5,
                    "bg_y": -15.2
                })
                self.assertEqual(res_slide.status_code, 200)
                self.assertTrue(res_slide.get_json()["ok"])
                slide_data = res_slide.get_json()["slide"]
                self.assertEqual(slide_data["duration"], 8500)
                self.assertEqual(slide_data["bg_zoom"], 150.0)
                self.assertEqual(slide_data["bg_x"], 10.5)
                self.assertEqual(slide_data["bg_y"], -15.2)

                # Rename background validation in dev mode
                res_rename = client.post("/panel/rename-background", json={"old_name": "inexistente.jpeg", "new_name": "nuevo.jpeg"})
                self.assertEqual(res_rename.status_code, 404)

                # Delete background validation in dev mode
                res_del = client.post("/panel/delete-background", json={"filename": "inexistente.jpeg"})
                self.assertEqual(res_del.status_code, 404)

                # Delete background rejection when in use by a slide
                with patch("app.load_slides", return_value=[{"id": "intro", "title": "Intro", "background": "sample.jpeg"}]):
                    res_in_use_bg = client.post("/panel/delete-background", json={"filename": "sample.jpeg"})
                    self.assertEqual(res_in_use_bg.status_code, 400)
                    self.assertIn("en uso", res_in_use_bg.get_json()["error"].lower())

            # 2. Production mode (dev mode False) -> 404 Not Found
            with patch("app.is_dev_mode", return_value=False):
                res = client.get("/panel")
                self.assertEqual(res.status_code, 404)

                res_post = client.post("/panel/slide/portada", data={"background": "photo01.jpeg"})
                self.assertEqual(res_post.status_code, 404)

                res_upload = client.post("/panel/upload-background")
                self.assertEqual(res_upload.status_code, 404)

                res_rename_prod = client.post("/panel/rename-background", json={"old_name": "test.jpeg", "new_name": "nuevo.jpeg"})
                self.assertEqual(res_rename_prod.status_code, 404)

                res_del_prod = client.post("/panel/delete-background", json={"filename": "test.jpeg"})
                self.assertEqual(res_del_prod.status_code, 404)

    def test_rsvp_special_slide_preservation_and_protection(self):
        from app import app, ensure_rsvp_slide
        with app.test_client() as client:
            with patch("app.is_dev_mode", return_value=True):
                # 1. Attempting to delete RSVP slide via API must be rejected with 400
                res_del = client.post("/panel/slide/rsvp/delete")
                self.assertEqual(res_del.status_code, 400)
                data = res_del.get_json()
                self.assertFalse(data["ok"])
                self.assertIn("especial", data["error"].lower())

                # 2. ensure_rsvp_slide restores RSVP if missing in a list
                slides_without_rsvp = [{"id": "portada", "title": "Portada"}]
                fixed = ensure_rsvp_slide(slides_without_rsvp)
                self.assertTrue(any(s["id"] == "rsvp" for s in fixed))
                rsvp_item = next(s for s in fixed if s["id"] == "rsvp")
                self.assertEqual(rsvp_item["template"], "slides/rsvp.html")
                self.assertTrue(rsvp_item["enabled"])

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
                    # 1. Production mode protection (404)
                    with patch("app.is_dev_mode", return_value=False):
                        self.assertEqual(client.get("/panel/styles/download").status_code, 404)
                        self.assertEqual(client.post("/panel/styles/upload").status_code, 404)
                        self.assertEqual(client.get("/panel/slides/download").status_code, 404)
                        self.assertEqual(client.post("/panel/slides/upload").status_code, 404)

                    # 2. Dev mode active
                    with patch("app.is_dev_mode", return_value=True):
                        # --- STYLES DOWNLOAD ---
                        res_dl_styles = client.get("/panel/styles/download")
                        self.assertEqual(res_dl_styles.status_code, 200)
                        self.assertIn("application/json", res_dl_styles.content_type)
                        self.assertIn("styles.json", res_dl_styles.headers.get("Content-Disposition", ""))
                        res_dl_styles.close()

                        # --- STYLES UPLOAD (JSON payload) ---
                        res_up_styles_json = client.post("/panel/styles/upload", json={
                            "title": {"fontFamily": "Cinzel", "fontSize": 42}
                        })
                        self.assertEqual(res_up_styles_json.status_code, 200)
                        data_styles = res_up_styles_json.get_json()
                        self.assertTrue(data_styles["ok"])
                        self.assertEqual(data_styles["styles"]["title"]["fontFamily"], "Cinzel")
                        self.assertEqual(data_styles["styles"]["title"]["fontSize"], 42)

                        # --- STYLES UPLOAD (File payload) ---
                        file_styles_data = io.BytesIO(b'{"tag": {"fontFamily": "Pacifico", "fontSize": 18}}')
                        res_up_styles_file = client.post("/panel/styles/upload", data={
                            "file": (file_styles_data, "styles.json")
                        })
                        self.assertEqual(res_up_styles_file.status_code, 200)
                        data_file_styles = res_up_styles_file.get_json()
                        self.assertTrue(data_file_styles["ok"])
                        self.assertEqual(data_file_styles["styles"]["tag"]["fontFamily"], "Pacifico")

                        # --- STYLES UPLOAD (Invalid data) ---
                        bad_file = io.BytesIO(b'{"invalid_json": ')
                        self.assertEqual(client.post("/panel/styles/upload", data={"file": (bad_file, "styles.json")}).status_code, 400)
                        self.assertEqual(client.post("/panel/styles/upload", json=["not a dict"]).status_code, 400)

                        # --- SLIDES DOWNLOAD ---
                        res_dl_slides = client.get("/panel/slides/download")
                        self.assertEqual(res_dl_slides.status_code, 200)
                        self.assertIn("application/json", res_dl_slides.content_type)
                        self.assertIn("slides.json", res_dl_slides.headers.get("Content-Disposition", ""))
                        res_dl_slides.close()

                        # --- SLIDES UPLOAD (JSON payload) ---
                        new_slides = [
                            {"id": "intro", "title": "Intro", "duration": 5000, "elements": []}
                        ]
                        res_up_slides = client.post("/panel/slides/upload", json=new_slides)
                        self.assertEqual(res_up_slides.status_code, 200)
                        data_slides = res_up_slides.get_json()
                        self.assertTrue(data_slides["ok"])
                        # Check that RSVP slide is guaranteed and preserved
                        self.assertTrue(any(s["id"] == "rsvp" for s in data_slides["slides"]))

                        # --- SLIDES UPLOAD (File payload) ---
                        file_slides_data = io.BytesIO(json.dumps([{"id": "portada", "title": "Portada", "elements": []}]).encode("utf-8"))
                        res_up_slides_file = client.post("/panel/slides/upload", data={
                            "file": (file_slides_data, "slides.json")
                        })
                        self.assertEqual(res_up_slides_file.status_code, 200)
                        self.assertTrue(res_up_slides_file.get_json()["ok"])

                        # --- SLIDES UPLOAD (Invalid data) ---
                        self.assertEqual(client.post("/panel/slides/upload", json={"not": "a list"}).status_code, 400)
                        bad_slides_file = io.BytesIO(b'["string instead of object"]')
                        self.assertEqual(client.post("/panel/slides/upload", data={"file": (bad_slides_file, "slides.json")}).status_code, 400)

    def test_panel_fonts_upload_and_delete(self):
        import io
        import tempfile
        from app import app

        with tempfile.TemporaryDirectory() as tmp_fonts_dir:
            with patch("app.get_fonts_dir", return_value=tmp_fonts_dir):
                with app.test_client() as client:
                    # 1. Production mode protection (404)
                    with patch("app.is_dev_mode", return_value=False):
                        self.assertEqual(client.post("/panel/upload-font").status_code, 404)
                        self.assertEqual(client.post("/panel/delete-font").status_code, 404)

                    # 2. Dev mode active
                    with patch("app.is_dev_mode", return_value=True):
                        # Missing file
                        res_empty = client.post("/panel/upload-font")
                        self.assertEqual(res_empty.status_code, 400)

                        # Bad extension
                        bad_file = (io.BytesIO(b"fake binary"), "bad.exe")
                        res_bad_ext = client.post("/panel/upload-font", data={"font_file": bad_file})
                        self.assertEqual(res_bad_ext.status_code, 400)

                        # Successful TTF upload
                        ttf_file = (io.BytesIO(b"dummy ttf content"), "SampleFont.ttf")
                        res_up = client.post("/panel/upload-font", data={"font_file": ttf_file, "font_name": "SampleFont"})
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
                        res_trav = client.post("/panel/delete-font", json={"filename": "../evil.ttf"})
                        self.assertEqual(res_trav.status_code, 400)

                        # Delete: Not found
                        res_nf = client.post("/panel/delete-font", json={"filename": "NonExistent.ttf"})
                        self.assertEqual(res_nf.status_code, 404)

                        # Delete: In-use protection
                        with patch("app.is_font_in_use", return_value=True):
                            res_in_use = client.post("/panel/delete-font", json={"filename": "SampleFont.ttf"})
                            self.assertEqual(res_in_use.status_code, 400)
                            self.assertIn("en uso", res_in_use.get_json()["error"].lower())

                        # Delete: Aveny-T system required sticker font protection
                        aveny_file = os.path.join(tmp_fonts_dir, "aveny-t-medium.ttf")
                        with open(aveny_file, "wb") as f:
                            f.write(b"font-data")
                        res_aveny = client.post("/panel/delete-font", json={"filename": "aveny-t-medium.ttf"})
                        self.assertEqual(res_aveny.status_code, 400)
                        self.assertIn("en uso", res_aveny.get_json()["error"].lower())
                        self.assertTrue(os.path.exists(aveny_file))

                        # Successful delete
                        res_del = client.post("/panel/delete-font", json={"filename": "SampleFont.ttf"})
                        self.assertEqual(res_del.status_code, 200)
                        self.assertTrue(res_del.get_json()["ok"])
                        self.assertFalse(os.path.exists(os.path.join(tmp_fonts_dir, "SampleFont.ttf")))


if __name__ == "__main__":
    unittest.main()
