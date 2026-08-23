import unittest
from unittest.mock import MagicMock, patch
from sheets import GoogleSheetsTable, FIELDNAMES, compute_check_code, generate_invitation_url, parse_and_validate_token


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
        new_record = {"apellido": "Lopez", "nombre": "Carlos", "asistencia": "si", "alimentacion": ["celiaco"], "invitacion_id": "amigos"}
        res = self.table.add_records([new_record])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["apellido"], "Lopez")
        self.assertEqual(res[0]["pa_celiaco"], "si")
        self.assertEqual(len(res[0]["id"]), 8)  # random 8 digit hex
        self.assertTrue(res[0]["url"].startswith("http://nos.vamos.acas.ar/i/amigos_"))
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

    def test_ensure_ids_and_urls(self):
        sample_data = [
            {"id": "", "url": "", "updated_at": "2026-08-12 12:00:00", "apellido": "Perez", "nombre": "Juan", "invitacion_id": "familia_perez"},
            {"id": "abc12345", "url": "http://nos.vamos.acas.ar/i/test_123456", "apellido": "Gomez", "nombre": "Maria", "confirmacion": "si"}
        ]
        self.mock_ws.get_all_records.return_value = sample_data

        records, updated_count = self.table.ensure_ids()
        self.assertEqual(updated_count, 1)
        self.assertEqual(len(records[0]["id"]), 8)
        self.assertTrue(records[0]["url"].startswith("http://nos.vamos.acas.ar/i/familia_perez_"))
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

        url = generate_invitation_url(inv_id)
        self.assertEqual(url, f"http://nos.vamos.acas.ar/i/familia_rodriguez_{code}")

        # Valid token validation
        valid_slug = parse_and_validate_token(f"familia_rodriguez_{code}")
        self.assertEqual(valid_slug, "familia_rodriguez")

        # Invalid token validation (forged check code)
        invalid_slug = parse_and_validate_token("familia_rodriguez_ffffff")
        self.assertIsNone(invalid_slug)

        # Token without check code
        no_code_slug = parse_and_validate_token("familia_rodriguez")
        self.assertIsNone(no_code_slug)


if __name__ == "__main__":
    unittest.main()









if __name__ == "__main__":
    unittest.main()
