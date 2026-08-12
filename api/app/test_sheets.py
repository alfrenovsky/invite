import unittest
from unittest.mock import MagicMock, patch
from sheets import GoogleSheetsTable, FIELDNAMES


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
        new_record = {"apellido": "Lopez", "nombre": "Carlos", "asistencia": "si", "alimentacion": ["celiaco"]}
        res = self.table.add_records([new_record])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["apellido"], "Lopez")
        self.assertEqual(res[0]["pa_celiaco"], "si")
        self.assertEqual(len(res[0]["id"]), 8)  # random 8 digit hex
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

    def test_ensure_ids(self):
        sample_data = [
            {"id": "", "apellido": "Perez", "nombre": "Juan", "confirmacion": "si"},
            {"id": "abc12345", "apellido": "Gomez", "nombre": "Maria", "confirmacion": "si"}
        ]
        self.mock_ws.get_all_records.return_value = sample_data

        records, updated_count = self.table.ensure_ids()
        self.assertEqual(updated_count, 1)
        self.assertEqual(len(records[0]["id"]), 8)
        # Row 2 hash: md5("row_2")[:8] == "09417486"
        self.assertEqual(records[0]["id"], "09417486")
        self.mock_ws.update.assert_called_once()

    def test_deterministic_row_id(self):
        self.mock_ws.get_all_records.return_value = []
        res = self.table.add_records([{"nombre": "Test"}])
        # First record inserted at row 2
        self.assertEqual(res[0]["id"], "09417486")





if __name__ == "__main__":
    unittest.main()
