import os
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

        self.table = GoogleSheetsTable(credentials_path="/dummy/path", sheet_id="dummy_sheet_id")

    def tearDown(self):
        self.patcher.stop()

    def test_get_all(self):
        sample_data = [
            {"id": "abc12345", "apellido": "Perez", "nombre": "Juan", "confirmacion": "si", "pa_general": "si"}
        ]
        self.mock_ws.get_all_records.return_value = sample_data
        result = self.table.get_all()
        self.assertEqual(result, sample_data)

    def test_get_by_id(self):
        sample_data = [
            {"id": "abc12345", "apellido": "Perez", "nombre": "Juan", "confirmacion": "si"},
            {"id": "def67890", "apellido": "Gomez", "nombre": "Maria", "confirmacion": "no"}
        ]
        self.mock_ws.get_all_records.return_value = sample_data

        found = self.table.get_by_id("def67890")
        self.assertIsNotNone(found)
        self.assertEqual(found["row_index"], 3)
        self.assertEqual(found["data"]["nombre"], "Maria")

    def test_add_records(self):
        new_record = {"apellido": "Lopez", "nombre": "Carlos", "telefono": "+54 9 11 1234-5678", "asistencia": "si", "alimentacion": ["celiaco"], "invitacion_id": "amigos"}
        res = self.table.add_records([new_record])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["apellido"], "Lopez")
        self.assertEqual(res[0]["pa_celiaco"], "si")
        self.assertEqual(len(res[0]["id"]), 8)  # random 8 digit hex
        self.assertTrue(res[0]["url"].startswith(f"{BASE_URL}/i/amigos_"))

        self.assertTrue(res[0]["whatsapp"].startswith("https://wa.me/5491112345678?text="))
        self.mock_ws.append_rows.assert_called_once()

    def test_update_record(self):
        sample_data = [
            {"id": "abc12345", "apellido": "Perez", "nombre": "Juan", "confirmacion": "si"}
        ]
        self.mock_ws.get_all_records.return_value = sample_data

        updated = self.table.update_record("abc12345", {"confirmacion": "no"})
        self.assertIsNotNone(updated)
        self.assertEqual(updated["confirmacion"], "no")
        self.mock_ws.update.assert_called_once()

    def test_delete_record(self):
        sample_data = [
            {"id": "abc12345", "apellido": "Perez", "nombre": "Juan", "confirmacion": "si"}
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

        records, updated_count = self.table.ensure_ids()
        self.assertEqual(updated_count, 1)
        self.assertEqual(len(records[0]["id"]), 8)
        self.assertTrue(records[0]["url"].startswith(f"{BASE_URL}/i/familia_perez_"))

        self.assertTrue(records[0]["whatsapp"].startswith("https://wa.me/5491198765432?text="))
        self.mock_ws.update.assert_called_once()

    def test_deterministic_row_time_id(self):
        self.mock_ws.get_all_records.return_value = []
        res = self.table.add_records([{"nombre": "Test", "updated_at": "2026-08-12 12:00:00"}])
        expected_id = self.table._generate_row_id(2, "2026-08-12 12:00:00")
        self.assertEqual(res[0]["id"], expected_id)

    def test_get_by_invitacion(self):
        sample_data = [
            {"id": "id1", "apellido": "Perez", "nombre": "Juan", "invitacion": "Familia Perez"},
            {"id": "id2", "apellido": "Perez", "nombre": "Maria", "invitacion": "Familia Perez"},
            {"id": "id3", "apellido": "Gomez", "nombre": "Carlos", "invitacion": "amigos_facu"}
        ]
        self.mock_ws.get_all_records.return_value = sample_data
        
        # Matches with underscore
        group1 = self.table.get_by_invitacion("familia_perez")
        self.assertEqual(len(group1), 2)
        self.assertEqual(group1[0]["nombre"], "Juan")

        # Matches with spaces directly
        group2 = self.table.get_by_invitacion("Familia Perez")
        self.assertEqual(len(group2), 2)

        # Matches with dashes
        group3 = self.table.get_by_invitacion("familia-perez")
        self.assertEqual(len(group3), 2)

    def test_check_code_and_token_validation(self):
        inv_id = "familia_rodriguez"
        code = compute_check_code(inv_id)
        self.assertEqual(len(code), 6)

        base_url = os.environ.get("BASE_URL", "http://nos.vamos.acas.ar")
        url = generate_invitation_url(inv_id)
        self.assertEqual(url, f"{base_url}/i/familia_rodriguez_{code}")


        # Valid token validation
        valid_slug = parse_and_validate_token(f"familia_rodriguez_{code}")
        self.assertEqual(valid_slug, "familia_rodriguez")

        # Invalid token validation (forged check code)
        invalid_slug = parse_and_validate_token("familia_rodriguez_ffffff")
        self.assertIsNone(invalid_slug)

        # Token without check code
        no_code_slug = parse_and_validate_token("familia_rodriguez")
        self.assertIsNone(no_code_slug)

    def test_whatsapp_url_generation(self):
        phone = "+54 9 (11) 2345-6789"
        url = "http://nos.vamos.acas.ar/i/test_123456"
        clean = clean_phone_number(phone)
        self.assertEqual(clean, "5491123456789")

        wa_url = generate_whatsapp_url(phone, url)
        self.assertEqual(wa_url, "https://wa.me/5491123456789?text=http%3A%2F%2Fnos.vamos.acas.ar%2Fi%2Ftest_123456")

        # Empty phone
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
                # Simulate WhatsApp crawler User-Agent
                headers = {"User-Agent": "WhatsApp/2.21.12.21 A"}
                res = client.get(f"/i/familia_perez_{code}", headers=headers)
                self.assertEqual(res.status_code, 200)
                # Verify sheets API was never called
                mock_get_by_inv.assert_not_called()
                # Verify OG metadata is in response
                self.assertIn(b"og:image", res.data)
                self.assertIn(b"whatsapp.thumb.jpeg", res.data)




    def test_api_key_protection_and_methods(self):
        from app import app
        with patch("app.table.get_all") as mock_get_all, patch("app.table.update_record") as mock_update:
            mock_get_all.return_value = [{"id": "1", "nombre": "Test"}]
            mock_update.return_value = {"id": "1", "nombre": "Test", "confirmacion": "si"}

            with app.test_client() as client:
                # 1. Accessing GET /invitados without key -> 401
                res = client.get("/invitados")
                self.assertEqual(res.status_code, 401)

                # 2. Accessing GET /invitados with X-API-Key header -> 200
                res = client.get("/invitados", headers={"X-API-Key": "boda_secret_api_key_2027"})
                self.assertEqual(res.status_code, 200)

                # 3. Accessing GET /invitados with Authorization Bearer header -> 200
                res = client.get("/invitados", headers={"Authorization": "Bearer boda_secret_api_key_2027"})
                self.assertEqual(res.status_code, 200)

                # 4. Accessing GET /invitados with query param ?api_key=... -> 200
                res = client.get("/invitados?api_key=boda_secret_api_key_2027")
                self.assertEqual(res.status_code, 200)

                # 5. Public PUT /invitados/1 (RSVP auto-save) does NOT require API key -> 200
    def test_dynamic_slides_manifest_and_single_slide(self):
        from app import app
        with patch("app.table.get_by_invitacion") as mock_get_by_inv:
            mock_get_by_inv.return_value = [
                {"id": "abc1", "nombre": "Juan", "apellido": "Perez", "confirmacion": "si", "url": "http://nos.vamos.acas.ar/i/familia_perez_123456"}
            ]
            with app.test_client() as client:
                code = compute_check_code("familia_perez")
                
                # 1. Fetch Manifest
                res = client.get(f"/i/familia_perez_{code}/slides")
                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertTrue(data["ok"])
                self.assertGreaterEqual(len(data["slides"]), 6)
                self.assertEqual(data["slides"][0]["id"], "portada")

                # 2. Fetch Single Slide (Portada)
                res_slide = client.get(f"/i/familia_perez_{code}/slide/portada")
                self.assertEqual(res_slide.status_code, 200)
                self.assertIn(b"Celia", res_slide.data)


                # 3. Fetch Single Slide (RSVP)
                res_rsvp = client.get(f"/i/familia_perez_{code}/slide/rsvp")
                self.assertEqual(res_rsvp.status_code, 200)
                self.assertIn(b"storyRsvpForm", res_rsvp.data)

                # 4. Invalid token -> 403
                res_invalid = client.get("/i/familia_perez_invalid/slides")
                self.assertEqual(res_invalid.status_code, 403)


if __name__ == "__main__":
    unittest.main()

