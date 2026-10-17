import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

import telegram_bot


class TestTelegramBot(unittest.TestCase):

    def setUp(self):
        # Create a temporary file for subscribers
        self.temp_dir = tempfile.TemporaryDirectory()
        self.subscribers_path = os.path.join(self.temp_dir.name, "subscribers.json")
        telegram_bot.SUBSCRIBERS_FILE = self.subscribers_path
        telegram_bot._subscribers_cache = None
        telegram_bot._last_opened.clear()
        telegram_bot._dirty_rsvp_sessions.clear()

        # Mock token and secret
        telegram_bot.TELEGRAM_BOT_TOKEN = "test_bot_token_123"
        telegram_bot.TELEGRAM_BOT_NAME = "celiayalfredo_bot"
        telegram_bot.TELEGRAM_AUTH_SECRET = "boda_cya_admin_telegram_2027"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_auth_link(self):
        link = telegram_bot.get_auth_link()
        self.assertEqual(link, "https://t.me/celiayalfredo_bot?start=boda_cya_admin_telegram_2027")

    def test_subscriber_management(self):
        self.assertFalse(telegram_bot.is_subscribed(123456))
        
        # Add subscriber
        added = telegram_bot.add_subscriber(123456, {"username": "alfredo", "first_name": "Alfredo"})
        self.assertTrue(added)
        self.assertTrue(telegram_bot.is_subscribed(123456))

        # Adding same subscriber again returns False
        self.assertFalse(telegram_bot.add_subscriber(123456))

        # Verify persisted on disk
        self.assertTrue(os.path.exists(self.subscribers_path))
        with open(self.subscribers_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(len(data["subscribers"]), 1)
            self.assertEqual(data["subscribers"][0]["chat_id"], 123456)

        # Remove subscriber
        removed = telegram_bot.remove_subscriber(123456)
        self.assertTrue(removed)
        self.assertFalse(telegram_bot.is_subscribed(123456))

    @patch("telegram_bot.send_message")
    def test_handle_start_unauthorized(self, mock_send):
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 99999},
                "from": {"first_name": "Intruder"},
                "text": "/start"
            }
        }
        telegram_bot._handle_update(update)
        self.assertFalse(telegram_bot.is_subscribed(99999))
        mock_send.assert_called_once()
        self.assertIn("Bot privado", mock_send.call_args[0][1])

    @patch("telegram_bot.send_message")
    def test_handle_start_authorized(self, mock_send):
        update = {
            "update_id": 2,
            "message": {
                "chat": {"id": 88888},
                "from": {"first_name": "Alfredo", "username": "alfrenovsky"},
                "text": "/start boda_cya_admin_telegram_2027"
            }
        }
        telegram_bot._handle_update(update)
        self.assertTrue(telegram_bot.is_subscribed(88888))
        mock_send.assert_called_once()
        self.assertIn("Autorizado con éxito", mock_send.call_args[0][1])

    @patch("telegram_bot.send_message")
    def test_handle_status_authorized(self, mock_send):
        # First subscribe
        telegram_bot.add_subscriber(88888)

        mock_table = MagicMock()
        sample_records = [
            {"nombre": "Celia", "dequien": "Celia Familia", "confirmacion": "si", "pagado": "si"},
            {"nombre": "Alfredo", "dequien": "Alfredo Amigos", "confirmacion": "si", "pagado": ""},
            {"nombre": "Ari", "dequien": "Alfredo Familia", "confirmacion": "no", "pagado": ""},
            {"nombre": "Invitado", "dequien": "Celia Amigos", "confirmacion": "", "pagado": ""},
        ]
        mock_table.get_all.return_value = sample_records
        mock_table.get_all_records.return_value = sample_records

        update = {
            "update_id": 3,
            "message": {
                "chat": {"id": 88888},
                "from": {"first_name": "Alfredo"},
                "text": "/status"
            }
        }
        telegram_bot._handle_update(update, table=mock_table)
        mock_table.flush_local_to_remote.assert_called_once()
        mock_table.get_all.assert_called_once_with(force_remote=True)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        self.assertIn("• Total: <b>4</b>", text)
        self.assertIn("• ✅ Confirmados: <b>2</b>", text)
        self.assertIn("• ❌ No asisten: <b>1</b>", text)
        self.assertIn("• ⏳ Pendientes: <b>1</b>", text)
        self.assertIn("👥 <b>Por origen (de quién):</b>", text)
        self.assertIn("• <b>Celia:</b> <b>2</b> (✅ 1 · ❌ 0 · ⏳ 1 · 💳 1)", text)
        self.assertIn("• <b>Alfredo:</b> <b>2</b> (✅ 1 · ❌ 1 · ⏳ 0 · 💳 0)", text)
        self.assertIn("• 📖 Abiertas: <b>0</b>", text)
        self.assertIn("💳 <b>Pagos:</b>", text)
        self.assertIn("• ✅ Grupos completos pagados: <b>0</b>", text)
        self.assertIn("• ⚠️ Invitaciones con pago parcial: <b>1</b>", text)
        self.assertIn("• 🎫 Invitaciones individuales pagadas: <b>1</b>", text)
        self.assertIn("Sincronizado con Google Sheets", text)

    @patch("telegram_bot.send_message")
    def test_handle_stop(self, mock_send):
        telegram_bot.add_subscriber(88888)
        self.assertTrue(telegram_bot.is_subscribed(88888))

        update = {
            "update_id": 4,
            "message": {
                "chat": {"id": 88888},
                "from": {"first_name": "Alfredo"},
                "text": "/stop"
            }
        }
        telegram_bot._handle_update(update)
        self.assertFalse(telegram_bot.is_subscribed(88888))
        mock_send.assert_called_once()
        self.assertIn("Notificaciones desactivadas", mock_send.call_args[0][1])

    @patch("telegram_bot.broadcast_message")
    def test_notify_invitation_opened_and_cooldown(self, mock_broadcast):
        guests = [{"nombre": "Celia", "apellido": "Muzaber"}]
        
        # 1st open: triggers notification
        telegram_bot.notify_invitation_opened("celia_y_alfredo", guests)
        self.assertEqual(mock_broadcast.call_count, 1)
        msg = mock_broadcast.call_args[0][0]
        self.assertIn("Invitación abierta", msg)
        self.assertIn("Celia Muzaber", msg)

        # 2nd open immediately after: suppressed by cooldown!
        telegram_bot.notify_invitation_opened("celia_y_alfredo", guests)
        self.assertEqual(mock_broadcast.call_count, 1)

        # 3rd open after 61 seconds (past 1-min cooldown): triggers notification
        now = time.time()
        with patch("time.time", return_value=now + 65):
            telegram_bot.notify_invitation_opened("celia_y_alfredo", guests)
            self.assertEqual(mock_broadcast.call_count, 2)

    @patch("telegram_bot.broadcast_message")
    def test_rsvp_dirty_session_and_flush_notification(self, mock_broadcast):
        # Modifying form marks session dirty with uppercase/spaces/dashes (no broadcast)
        telegram_bot.mark_invitation_dirty("Celia_y_Alfredo")
        self.assertEqual(mock_broadcast.call_count, 0)

        # Leaving form calls flush with lowercase slug -> triggers single consolidated broadcast
        guests = [
            {"nombre": "Celia", "apellido": "Muzaber", "confirmacion": "si", "menu": "general", "celiaco": "si"},
            {"nombre": "Alfredo", "apellido": "Rezinovsky", "confirmacion": "si", "menu": "general", "celiaco": ""},
            {"nombre": "Ari", "apellido": "Gato", "confirmacion": "no"}
        ]
        telegram_bot.notify_rsvp_on_flush("celia_y_alfredo", guests)
        self.assertEqual(mock_broadcast.call_count, 1)
        msg = mock_broadcast.call_args[0][0]
        self.assertIn("Confirmación RSVP actualizada", msg)
        self.assertIn("Celia Muzaber", msg)
        self.assertIn("✅ Asiste (Menú: General, 🌾 Sin gluten)", msg)
        self.assertIn("Alfredo Rezinovsky", msg)
        self.assertIn("Ari Gato", msg)
        self.assertIn("❌ No asiste", msg)

        # Next flush without changes -> does NOT broadcast again (no spam)
        telegram_bot.notify_rsvp_on_flush("celia_y_alfredo", guests)
        self.assertEqual(mock_broadcast.call_count, 1)

    @patch("telegram_bot.broadcast_message")
    def test_notify_comprobante_uploaded(self, mock_broadcast):
        guests = [{"nombre": "Celia", "apellido": "Muzaber"}]
        telegram_bot.notify_comprobante_uploaded("celia_y_alfredo", "comprobante_123.pdf", "https://example.com/comprobante_123.pdf", guests)
        self.assertEqual(mock_broadcast.call_count, 1)
        msg = mock_broadcast.call_args[0][0]
        self.assertIn("Comprobante de pago recibido", msg)
        self.assertIn("comprobante_123.pdf", msg)
        self.assertIn("https://example.com/comprobante_123.pdf", msg)

    @patch("telegram_bot.send_message")
    def test_handle_webhook_update(self, mock_send):
        update = {
            "update_id": 10,
            "message": {
                "chat": {"id": 11111},
                "from": {"first_name": "Alfredo"},
                "text": "/start boda_cya_admin_telegram_2027"
            }
        }
        res = telegram_bot.handle_webhook_update(update)
        self.assertTrue(res)
        self.assertTrue(telegram_bot.is_subscribed(11111))
        mock_send.assert_called_once()

    def test_flask_webhook_endpoint(self):
        from app import app
        client = app.test_client()

        # 1. Invalid secret token header -> 403
        res = client.post(
            "/telegram/webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret"}
        )
        self.assertEqual(res.status_code, 403)

        # 2. Valid secret token header -> 200
        with patch("telegram_bot.handle_webhook_update") as mock_handler:
            mock_handler.return_value = True
            res = client.post(
                "/telegram/webhook",
                json={"update_id": 1},
                headers={"X-Telegram-Bot-Api-Secret-Token": "boda_cya_admin_telegram_2027"}
            )
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data["ok"])
            mock_handler.assert_called_once()

    @patch("telegram_bot.send_message")
    def test_handle_send_command(self, mock_send):
        telegram_bot.add_subscriber(88888)
        mock_table = MagicMock()
        mock_table.get_all.return_value = [
            {"id": "1", "nombre": "Camila", "apellido": "Perez", "dequien": "Celia Familia", "telefono": "1111", "abierto": "", "url": "http://nos.vamos.acas.ar/i/camila_123"},
            {"id": "2", "nombre": "Sofia", "apellido": "Gomez", "dequien": "Celia Amigos", "telefono": "2222", "abierto": "si", "url": "http://nos.vamos.acas.ar/i/sofia_123"},
            {"id": "3", "nombre": "Lucia", "apellido": "Lopez", "dequien": "Celia Trabajo", "telefono": "", "abierto": "", "url": "http://nos.vamos.acas.ar/i/lucia_123"},
            {"id": "4", "nombre": "Martin", "apellido": "Diaz", "dequien": "Alfredo Amigos", "telefono": "3333", "abierto": "", "url": "http://nos.vamos.acas.ar/i/martin_123"},
        ]

        # 1. /send with no argument -> usage instructions
        telegram_bot._handle_update({"message": {"chat": {"id": 88888}, "text": "/send"}}, table=mock_table)
        self.assertIn("Uso del comando /send", mock_send.call_args[0][1])

        # 2. /send Celia -> includes Camila (has phone, not opened), excludes Sofia (opened) and Lucia (no phone)
        mock_send.reset_mock()
        telegram_bot._handle_update({"message": {"chat": {"id": 88888}, "text": "/send Celia"}}, table=mock_table)
        call_text = mock_send.call_args[0][1]
        self.assertIn("Camila Perez", call_text)
        self.assertIn("wa.me/1111", call_text)
        self.assertIn('<a href="https://wa.me/1111', call_text)
        self.assertNotIn("Sofia Gomez", call_text)
        self.assertNotIn("Lucia Lopez", call_text)

        # 3. /send Alfredo -> includes Martin
        mock_send.reset_mock()
        telegram_bot._handle_update({"message": {"chat": {"id": 88888}, "text": "/send Alfredo"}}, table=mock_table)
        self.assertIn("Martin Diaz", mock_send.call_args[0][1])

        # 4. /send Amigos -> no matches
        mock_send.reset_mock()
        telegram_bot._handle_update({"message": {"chat": {"id": 88888}, "text": "/send Amigos"}}, table=mock_table)
        self.assertIn("No hay invitaciones pendientes", mock_send.call_args[0][1])

    @patch("telegram_bot.send_message")
    def test_handle_sintelefono_command(self, mock_send):
        telegram_bot.add_subscriber(88888)
        mock_table = MagicMock()
        mock_table.get_all.return_value = [
            # Group 1: fam_garcia (Celia) - 2 guests, NO phone
            {"id": "1", "invitacion_id": "fam_garcia", "nombre": "Juan", "apellido": "Garcia", "dequien": "Celia Familia", "telefono": "", "url": "http://test/i/fam_garcia_111"},
            {"id": "2", "invitacion_id": "fam_garcia", "nombre": "Maria", "apellido": "Garcia", "dequien": "Celia Familia", "telefono": "", "url": "http://test/i/fam_garcia_111"},
            # Group 2: fam_perez (Alfredo) - 1 guest with phone, 1 without
            {"id": "3", "invitacion_id": "fam_perez", "nombre": "Carlos", "apellido": "Perez", "dequien": "Alfredo Amigos", "telefono": "4444", "url": "http://test/i/fam_perez_222"},
            {"id": "4", "invitacion_id": "fam_perez", "nombre": "Ana", "apellido": "Perez", "dequien": "Alfredo Amigos", "telefono": "", "url": "http://test/i/fam_perez_222"},
            # Group 3: amigos_rock (Amigos) - 1 guest, NO phone
            {"id": "5", "invitacion_id": "amigos_rock", "nombre": "Diego", "apellido": "Torres", "dequien": "Amigos Banda", "telefono": "", "url": "http://test/i/amigos_rock_333"},
        ]

        # 1. /sintelefono (summary)
        telegram_bot._handle_update({"message": {"chat": {"id": 88888}, "text": "/sintelefono"}}, table=mock_table)
        summary_text = mock_send.call_args[0][1]
        self.assertIn("Tarjetas sin teléfono", summary_text)
        self.assertIn("Celia:", summary_text)
        self.assertIn("Amigos:", summary_text)

        # 2. /sintelefono Celia -> returns fam_garcia with URL and names
        mock_send.reset_mock()
        telegram_bot._handle_update({"message": {"chat": {"id": 88888}, "text": "/sintelefono Celia"}}, table=mock_table)
        celia_text = mock_send.call_args[0][1]
        self.assertIn("fam_garcia", celia_text)
        self.assertIn("Juan Garcia, Maria Garcia", celia_text)
        self.assertIn("http://test/i/fam_garcia_111", celia_text)
        self.assertNotIn("amigos_rock", celia_text)
        self.assertNotIn("fam_perez", celia_text)

        # 3. /sintelefono todos -> includes both fam_garcia and amigos_rock, excludes fam_perez
        mock_send.reset_mock()
        telegram_bot._handle_update({"message": {"chat": {"id": 88888}, "text": "/sintelefono todos"}}, table=mock_table)
        todos_text = mock_send.call_args[0][1]
        self.assertIn("fam_garcia", todos_text)
        self.assertIn("amigos_rock", todos_text)
        self.assertNotIn("fam_perez", todos_text)


if __name__ == "__main__":
    unittest.main()
