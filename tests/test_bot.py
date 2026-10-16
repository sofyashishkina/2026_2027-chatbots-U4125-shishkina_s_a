"""Проверки без токена и сетевых запросов: python -m unittest discover -s tests -v."""

import logging
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
from telegram import User
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot as app


def user(telegram_id=42, name="Иван"):
    return User(telegram_id, name, False, username=f"user{telegram_id}")


def settings():
    return app.Settings(
        bot_token="123:test", admin_id=42,
        booking_contact="@example", booking_url="https://t.me/example",
        phone="+70000000000",
    )


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "data" / "bot.db"
        self.store = app.UserStore(self.path)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def row(self, telegram_id=42):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()

    def test_start_deduplicates_updates_profile_and_preserves_first_seen(self):
        self.store.register(user())
        initial = self.row()
        self.store.register(user(name="Новое имя"))
        current = self.row()
        with closing(sqlite3.connect(self.path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(current["first_seen_at"], initial["first_seen_at"])
        self.assertEqual(current["first_name"], "Новое имя")
        self.assertTrue(current["last_seen_at"])
        self.assertEqual(self.store.active_ids(), [42])

    def test_blocked_subscription_persists_and_start_reactivates_it(self):
        self.store.register(user())
        self.store.deactivate(42)
        self.assertEqual(self.row()["is_active"], 0)
        self.assertTrue(self.row()["blocked_at"])
        self.store.close()
        self.store = app.UserStore(self.path)
        self.assertEqual(self.store.active_ids(), [])
        self.store.register(user())
        self.assertEqual(self.store.active_ids(), [42])
        self.assertIsNone(self.row()["blocked_at"])

    def test_activity_does_not_subscribe_or_reactivate_without_start(self):
        self.store.touch(user())
        self.assertIsNone(self.row())
        self.store.register(user())
        self.store.deactivate(42)
        self.store.touch(user())
        self.assertEqual(self.store.active_ids(), [])


class ImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        quiet_logs = patch.object(app.LOGGER, "disabled", True)
        quiet_logs.start()
        self.addCleanup(quiet_logs.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.bot = SimpleNamespace(send_photo=AsyncMock(), send_media_group=AsyncMock())

    def tearDown(self):
        self.temporary.cleanup()

    def picture(self, name, size=(20, 20), color="white"):
        path = self.directory / name
        Image.new("RGB", size, color).save(path)
        return path

    def test_discovery_accepts_supported_extensions_and_sorts_names(self):
        for name in ["22.PNG", "02.jpeg", "01.jpg", "10.JPG", "B.jpg", "a.jpg"]:
            self.picture(name)
        (self.directory / "readme.txt").write_text("Не фотография", encoding="utf-8")
        (self.directory / "03.png").mkdir()
        self.assertEqual(
            [path.name for path in app.load_images(self.directory)],
            ["01.jpg", "02.jpeg", "10.JPG", "22.PNG", "a.jpg", "B.jpg"],
        )
        self.assertEqual(app.load_images(self.directory / "missing"), [])

    def test_photo_validation_rejects_damage_and_telegram_limits(self):
        valid = self.picture("valid.png")
        self.assertEqual(app.read_photo(valid), valid.read_bytes())
        damaged = self.directory / "damaged.jpg"
        damaged.write_bytes(b"not an image")
        self.assertIsNone(app.read_photo(damaged))
        self.assertIsNone(app.read_photo(self.directory / "missing.jpg"))
        self.assertIsNone(app.read_photo(self.picture("wide.png", (500, 10))))
        self.assertIsNone(app.read_photo(self.picture("large_dimensions.png", (6000, 5000))))
        oversized = self.directory / "oversized.jpg"
        oversized.write_bytes(valid.read_bytes())
        with oversized.open("r+b") as output:
            output.truncate(10 * 1024 * 1024 + 1)
        self.assertIsNone(app.read_photo(oversized))

    async def test_albums_respect_telegram_limits_for_every_remainder(self):
        paths = [self.picture(f"{index:02}.png") for index in range(1, 23)]
        for count in [0, 1, 10, 11, 21, 22]:
            with self.subTest(count=count):
                self.bot.send_photo.reset_mock()
                self.bot.send_media_group.reset_mock()
                sent = await app.send_images(self.bot, 42, paths[:count])
                album_sizes = [
                    len(call.kwargs["media"])
                    for call in self.bot.send_media_group.await_args_list
                ]
                self.assertEqual(sent, count)
                self.assertTrue(all(2 <= size <= 10 for size in album_sizes))
                self.assertEqual(sum(album_sizes) + self.bot.send_photo.await_count, count)
                self.assertEqual(self.bot.send_photo.await_count, int(count % 10 == 1))

    async def test_damaged_image_does_not_hide_remaining_cards(self):
        first = self.picture("01.png", color="red")
        damaged = self.directory / "02.png"
        damaged.write_text("broken", encoding="utf-8")
        third = self.picture("03.png", color="blue")
        sent = await app.send_images(self.bot, 42, [first, damaged, third])
        self.assertEqual(sent, 2)
        media = self.bot.send_media_group.await_args.kwargs["media"]
        contents = [item.media.input_file_content for item in media]
        self.assertEqual(contents, [first.read_bytes(), third.read_bytes()])

    async def test_rejected_album_falls_back_to_individual_photos(self):
        paths = [self.picture(f"{index}.png") for index in range(3)]
        self.bot.send_media_group.side_effect = BadRequest("Failed to send media group")
        sent = await app.send_images(self.bot, 42, paths)
        self.assertEqual(sent, 3)
        self.assertEqual(self.bot.send_photo.await_count, 3)


class BroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        quiet_logs = patch.object(app.LOGGER, "disabled", True)
        quiet_logs.start()
        self.addCleanup(quiet_logs.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.store = app.UserStore(Path(self.temporary.name) / "bot.db")
        self.bot = SimpleNamespace(
            copy_message=AsyncMock(return_value=SimpleNamespace(message_id=80)),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=100)),
            edit_message_reply_markup=AsyncMock(),
        )
        self.bot_data = {
            "store": self.store, "settings": settings(), "broadcast_running": False,
        }
        self.scheduled = []

        def capture_task(coroutine, **kwargs):
            self.scheduled.append(coroutine)
            coroutine.close()
            return Mock()

        self.application = SimpleNamespace(
            bot=self.bot, bot_data=self.bot_data, create_task=Mock(side_effect=capture_task),
        )
        self.context = SimpleNamespace(
            bot=self.bot, bot_data=self.bot_data, user_data={}, application=self.application,
        )

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def update(self, telegram_id=42, data=None, confirmation_id=100, chat_type="private"):
        chat = SimpleNamespace(id=telegram_id, type=chat_type)
        message = SimpleNamespace(
            message_id=confirmation_id, chat_id=telegram_id, chat=chat,
            text="Сообщение для рассылки", photo=[], video=None,
            media_group_id=None,
            reply_text=AsyncMock(return_value=SimpleNamespace(message_id=100)),
        )
        query = None if data is None else SimpleNamespace(
            data=data, from_user=user(telegram_id), message=message,
            answer=AsyncMock(), edit_message_text=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        return SimpleNamespace(
            effective_user=user(telegram_id), effective_chat=chat,
            effective_message=message, message=message, callback_query=query,
        )

    def draft(self):
        return app.BroadcastDraft(42, 7, "current-token", confirmation_message_id=100)

    async def test_delivery_continues_after_errors_and_only_blocks_unreachable_users(self):
        for telegram_id in range(1, 6):
            self.store.register(user(telegram_id))
        outcomes = {
            1: Forbidden("Forbidden: bot was blocked by the user"),
            2: BadRequest("Chat not found"),
            3: BadRequest("Wrong file identifier/HTTP URL specified"),
            4: NetworkError("Temporary connection failure"),
        }

        async def deliver(**kwargs):
            error = outcomes.get(kwargs["chat_id"])
            if error:
                raise error
            return SimpleNamespace(message_id=8)

        self.bot.copy_message.side_effect = deliver
        self.bot_data["broadcast_running"] = True
        with patch.object(app.asyncio, "sleep", new_callable=AsyncMock):
            await app.run_broadcast(self.application, self.draft(), 42)
        self.assertEqual(self.bot.copy_message.await_count, 5)
        self.assertCountEqual(self.store.active_ids(), [3, 4, 5])
        summary = self.bot.send_message.await_args.kwargs["text"]
        self.assertIn("Всего пользователей: 5", summary)
        self.assertIn("Успешно отправлено: 1", summary)
        self.assertIn("Не удалось отправить: 4", summary)
        self.assertFalse(self.bot_data["broadcast_running"])

    async def test_retry_after_waits_and_retries_without_duplicate_success(self):
        operation = AsyncMock(side_effect=[RetryAfter(1), "sent"])
        with patch.object(app.asyncio, "sleep", new_callable=AsyncMock) as pause:
            result = await app.call_with_retry(operation)
        self.assertEqual(result, "sent")
        self.assertEqual(operation.await_count, 2)
        pause.assert_awaited_once()

    async def test_retry_after_has_a_bounded_number_of_attempts(self):
        operation = AsyncMock(side_effect=RetryAfter(1))
        with patch.object(app.asyncio, "sleep", new_callable=AsyncMock):
            with self.assertRaises(RetryAfter):
                await app.call_with_retry(operation)
        self.assertEqual(operation.await_count, 3)

    async def test_network_failure_is_not_retried_to_avoid_duplicate_delivery(self):
        operation = AsyncMock(side_effect=NetworkError("Connection lost"))
        with self.assertRaises(NetworkError):
            await app.call_with_retry(operation)
        operation.assert_awaited_once()

    async def test_start_registers_subscriber_and_replies_with_menu(self):
        update = self.update()
        await app.start(update, self.context)
        self.assertEqual(self.store.active_ids(), [42])
        self.bot.send_message.assert_awaited()
        replies = self.bot.send_message.await_args_list
        self.assertTrue(any(call.kwargs.get("reply_markup") for call in replies))

    async def test_broadcast_command_is_admin_private_only(self):
        for telegram_id, chat_type in [(99, "private"), (42, "group")]:
            with self.subTest(telegram_id=telegram_id, chat_type=chat_type):
                self.context.user_data.clear()
                await app.broadcast_command(self.update(telegram_id, chat_type=chat_type), self.context)
                self.assertFalse(self.context.user_data.get("broadcast_waiting"))
        await app.broadcast_command(self.update(), self.context)
        self.assertTrue(self.context.user_data["broadcast_waiting"])

    async def test_text_photo_and_video_are_previewed_before_confirmation(self):
        for kind in ["text", "photo", "video"]:
            with self.subTest(kind=kind):
                self.bot.copy_message.reset_mock()
                self.context.user_data = {"broadcast_waiting": True}
                update = self.update()
                message = update.effective_message
                message.text = "Новость" if kind == "text" else None
                message.photo = [SimpleNamespace(file_id="photo")] if kind == "photo" else []
                message.video = SimpleNamespace(file_id="video") if kind == "video" else None
                await app.receive_message(update, self.context)
                self.bot.copy_message.assert_awaited_once_with(
                    chat_id=42, from_chat_id=42, message_id=100,
                )
                draft = self.context.user_data["broadcast_draft"]
                self.assertEqual(draft.source_message_id, 80)
                self.assertFalse(self.context.user_data["broadcast_waiting"])
                keyboard = self.bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard
                actions = {button.callback_data for row in keyboard for button in row}
                self.assertEqual(actions, {
                    f"broadcast:send:{draft.token}", f"broadcast:cancel:{draft.token}",
                })
                self.application.create_task.assert_not_called()

    async def test_unsupported_attachment_or_album_stays_in_draft_mode(self):
        for kind in ["unsupported", "album"]:
            with self.subTest(kind=kind):
                self.context.user_data = {"broadcast_waiting": True}
                update = self.update()
                message = update.effective_message
                message.text = None
                message.photo = [SimpleNamespace(file_id="photo")] if kind == "album" else []
                message.media_group_id = "album-1" if kind == "album" else None
                await app.receive_message(update, self.context)
                self.assertTrue(self.context.user_data["broadcast_waiting"])
                self.assertNotIn("broadcast_draft", self.context.user_data)
        self.bot.copy_message.assert_not_awaited()

    async def test_non_admin_cannot_send_or_cancel_admin_draft(self):
        for action in ["send", "cancel"]:
            with self.subTest(action=action):
                draft = self.draft()
                self.context.user_data["broadcast_draft"] = draft
                update = self.update(99, f"broadcast:{action}:current-token")
                await app.broadcast_callback(update, self.context)
                self.assertIs(self.context.user_data["broadcast_draft"], draft)
                update.callback_query.answer.assert_awaited()
        self.application.create_task.assert_not_called()

    async def test_stale_confirmation_or_cancel_cannot_change_the_current_draft(self):
        for action in ["send", "cancel"]:
            for token, message_id in [("obsolete-token", 100), ("current-token", 99)]:
                with self.subTest(action=action, token=token, message_id=message_id):
                    draft = self.draft()
                    self.context.user_data["broadcast_draft"] = draft
                    update = self.update(data=f"broadcast:{action}:{token}", confirmation_id=message_id)
                    await app.broadcast_callback(update, self.context)
                    update.callback_query.answer.assert_awaited()
                    self.assertIs(self.context.user_data["broadcast_draft"], draft)
        self.application.create_task.assert_not_called()

    async def test_confirmation_starts_delivery_once_even_after_repeated_click(self):
        self.context.user_data["broadcast_draft"] = self.draft()
        update = self.update(data="broadcast:send:current-token")
        await app.broadcast_callback(update, self.context)
        await app.broadcast_callback(update, self.context)
        self.application.create_task.assert_called_once()
        self.assertTrue(self.bot_data["broadcast_running"])
        self.assertNotIn("broadcast_draft", self.context.user_data)
        self.assertEqual(update.callback_query.answer.await_count, 2)

    async def test_cancel_discards_draft_without_sending(self):
        self.context.user_data["broadcast_draft"] = self.draft()
        await app.broadcast_callback(self.update(data="broadcast:cancel:current-token"), self.context)
        self.assertNotIn("broadcast_draft", self.context.user_data)
        self.application.create_task.assert_not_called()
        self.bot.copy_message.assert_not_awaited()


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        quiet_logs = patch.object(app.LOGGER, "disabled", True)
        quiet_logs.start()
        self.addCleanup(quiet_logs.stop)
        self.temporary = tempfile.TemporaryDirectory()
        assets = patch.object(app, "ASSETS_DIR", Path(self.temporary.name))
        assets.start()
        self.addCleanup(assets.stop)
        self.bot = SimpleNamespace(
            send_message=AsyncMock(), send_photo=AsyncMock(), send_media_group=AsyncMock(),
        )
        self.context = SimpleNamespace(bot=self.bot, bot_data={"settings": settings()})
        self.update = SimpleNamespace(effective_chat=SimpleNamespace(id=42, type="private"))

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def buttons(markup):
        return [button for row in markup.inline_keyboard for button in row]

    async def test_every_section_opens_without_assets_and_offers_main_menu(self):
        sections = {
            "catalog": "КАТАЛОГ МАСТЕР-КЛАССОВ", "price": "ПРАЙС YOU ART",
            "events": "АФИША YOU ART", "party": "ПРАЗДНИКИ В YOU ART",
            "signup": "ЗАПИСЬ НА МАСТЕР-КЛАСС", "booking": "УСЛОВИЯ БРОНИРОВАНИЯ",
            "faq": "ЧАСТЫЕ ВОПРОСЫ",
            "certificates": "ПОДАРОЧНЫЕ СЕРТИФИКАТЫ", "contacts": "КАК НАС НАЙТИ",
        }
        for section, title in sections.items():
            with self.subTest(section=section):
                self.bot.send_message.reset_mock()
                await app.show_section(self.update, self.context, section)
                messages = self.bot.send_message.await_args_list
                self.assertIn(title, messages[0].kwargs["text"])
                markup = messages[-1].kwargs["reply_markup"]
                destinations = {button.callback_data for button in self.buttons(markup)}
                self.assertIn("section:menu", destinations)
                if section in ("price", "events"):
                    self.assertGreaterEqual(len(messages), 2)
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_media_group.assert_not_awaited()

    def test_main_menu_has_all_nine_distinct_sections(self):
        buttons = self.buttons(app.keyboard(settings()))
        self.assertEqual(len(buttons), 9)
        self.assertEqual({button.callback_data for button in buttons}, {
            f"section:{section}" for section in (
                "catalog", "price", "events", "party", "signup", "booking", "faq", "certificates", "contacts",
            )
        })

    def test_shortcuts_and_configured_contact_links_have_valid_destinations(self):
        configured = replace(
            settings(), vk_url="https://vk.com/youart", telegram_url="https://t.me/youart",
            map_url="https://example.com/map", booking_url="https://t.me/newcontact",
        )
        expected = {
            "price": {"signup", "catalog", "menu"},
            "events": {"signup", "menu"},
            "party": {"catalog", "signup", "booking", "menu"},
            "signup": {"catalog", "price", "menu"},
            "booking": {"signup", "faq", "menu"},
            "faq": {"catalog", "menu"},
            "certificates": {"catalog", "menu"},
            "contacts": {"menu"},
        }
        for section, destinations in expected.items():
            with self.subTest(section=section):
                buttons = self.buttons(app.keyboard(configured, section))
                self.assertEqual(
                    {button.callback_data for button in buttons if button.callback_data},
                    {f"section:{destination}" for destination in destinations},
                )
                self.assertTrue(all(bool(button.url) != bool(button.callback_data) for button in buttons))
                if section in ("signup", "faq", "certificates"):
                    self.assertIn(configured.booking_url, {button.url for button in buttons})
        contact_urls = {button.url for button in self.buttons(app.keyboard(configured, "contacts")) if button.url}
        self.assertEqual(contact_urls, {
            configured.booking_url, configured.vk_url, configured.telegram_url, configured.map_url,
        })
        blank_social_urls = {
            button.url for button in self.buttons(app.keyboard(settings(), "contacts")) if button.url
        }
        self.assertEqual(blank_social_urls, {settings().booking_url})

    async def test_configured_contacts_are_escaped_in_html(self):
        self.context.bot_data["settings"] = replace(
            settings(), booking_contact="<Иван & Софья>", phone="+7 <913> & 000",
            booking_url='https://t.me/example?x="yes"&y=<tag>',
        )
        await app.show_section(self.update, self.context, "signup")
        text = self.bot.send_message.await_args.kwargs["text"]
        self.assertIn("&lt;Иван &amp; Софья&gt;", text)
        self.assertIn('href="https://t.me/example?x=&quot;yes&quot;&amp;y=&lt;tag&gt;"', text)
        self.assertNotIn("<Иван", text)
        await app.show_section(self.update, self.context, "contacts")
        self.assertIn("+7 &lt;913&gt; &amp; 000", self.bot.send_message.await_args.kwargs["text"])

    async def test_uppercase_command_with_bot_mention_opens_catalog(self):
        self.update.effective_message = SimpleNamespace(text="/CATALOG@YouArtBot аргумент")
        with patch.object(app, "show_section", new_callable=AsyncMock) as show:
            await app.section_command(self.update, self.context)
        show.assert_awaited_once_with(self.update, self.context, "catalog")


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        quiet_logs = patch.object(app.LOGGER, "disabled", True)
        quiet_logs.start()
        self.addCleanup(quiet_logs.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        assets = patch.object(app, "ASSETS_DIR", Path(self.temporary.name))
        assets.start()
        self.addCleanup(assets.stop)
        self.bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
        self.context = SimpleNamespace(bot=self.bot, bot_data={"settings": settings()})
        self.update = SimpleNamespace(effective_chat=SimpleNamespace(id=42, type="private"))

    def card_photo(self, workshop):
        path = Path(self.temporary.name) / "catalog" / workshop.photo
        path.parent.mkdir(exist_ok=True)
        Image.new("RGB", (20, 25), "white").save(path, "JPEG")
        return path

    def assert_card_buttons(self, markup):
        buttons = NavigationTests.buttons(markup)
        self.assertIn(settings().booking_url, {button.url for button in buttons})
        self.assertIn("section:catalog", {button.callback_data for button in buttons})

    def test_catalog_lists_every_workshop_once_with_existing_photo(self):
        buttons = NavigationTests.buttons(app.keyboard(settings(), "catalog"))
        workshop_callbacks = [button.callback_data for button in buttons
                              if button.callback_data.startswith("workshop:")]
        self.assertEqual(workshop_callbacks, [f"workshop:{workshop.slug}" for workshop in app.WORKSHOPS])
        self.assertEqual(len(set(workshop_callbacks)), len(app.WORKSHOPS))
        self.assertIn("section:menu", {button.callback_data for button in buttons})
        # Telegram ограничивает callback_data 64 байтами.
        self.assertTrue(all(len(callback.encode()) <= 64 for callback in workshop_callbacks))
        for workshop in app.WORKSHOPS:
            with self.subTest(workshop=workshop.slug):
                self.assertTrue((app.BASE_DIR / "assets" / "catalog" / workshop.photo).is_file())

    def test_every_caption_fits_telegram_limit_and_has_key_facts(self):
        for workshop in app.WORKSHOPS:
            with self.subTest(workshop=workshop.slug):
                caption = app.workshop_caption(workshop)
                self.assertLessEqual(len(caption), 1024)
                self.assertIn(workshop.duration, caption)
                self.assertIn(app.GROUP_SIZE, caption)
                for price in workshop.prices:
                    self.assertIn(price, caption)

    async def test_card_is_sent_as_photo_with_caption_and_buttons(self):
        workshop = app.WORKSHOPS_BY_SLUG["painting"]
        photo = self.card_photo(workshop)
        await app.show_workshop(self.update, self.context, "painting")
        kwargs = self.bot.send_photo.await_args.kwargs
        self.assertEqual(kwargs["photo"], photo.read_bytes())
        self.assertIn("Живопись на холсте", kwargs["caption"])
        self.assertIn("холст 30×40 — 2600 ₽", kwargs["caption"])
        self.assertIn("от 12 лет", kwargs["caption"])
        self.assert_card_buttons(kwargs["reply_markup"])
        self.bot.send_message.assert_not_awaited()

    async def test_card_falls_back_to_text_without_photo_or_after_rejection(self):
        workshop = app.WORKSHOPS_BY_SLUG["soap"]
        for case in ("missing", "rejected"):
            with self.subTest(case=case):
                self.bot.send_message.reset_mock()
                if case == "rejected":
                    self.card_photo(workshop)
                    self.bot.send_photo.side_effect = BadRequest("Wrong file")
                await app.show_workshop(self.update, self.context, "soap")
                kwargs = self.bot.send_message.await_args.kwargs
                self.assertEqual(kwargs["text"], app.workshop_caption(workshop))
                self.assert_card_buttons(kwargs["reply_markup"])

    async def test_unknown_workshop_offers_main_menu(self):
        await app.show_workshop(self.update, self.context, "missing")
        kwargs = self.bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["text"], app.TEXT["error"])
        self.assertEqual(kwargs["reply_markup"], app.keyboard(settings()))
        self.bot.send_photo.assert_not_awaited()

    async def test_workshop_button_opens_its_card_only_in_private_chat(self):
        query = SimpleNamespace(data="workshop:tiffany", answer=AsyncMock())
        self.update.callback_query = query
        with patch.object(app, "show_workshop", new_callable=AsyncMock) as show:
            await app.workshop_callback(self.update, self.context)
            show.assert_awaited_once_with(self.update, self.context, "tiffany")
            self.update.effective_chat.type = "group"
            await app.workshop_callback(self.update, self.context)
            show.assert_awaited_once()
        self.assertEqual(query.answer.await_count, 2)

    def test_bot_description_texts_fit_telegram_limits(self):
        self.assertLessEqual(len(app.TEXT["description"]), 512)
        self.assertLessEqual(len(app.TEXT["about"]), 120)


class LoggingTests(unittest.TestCase):
    def test_bot_token_is_redacted_inside_telegram_api_url(self):
        token = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789"
        record = logging.LogRecord(
            "httpx", logging.WARNING, __file__, 1,
            f"Request failed: https://api.telegram.org/bot{token}/getMe", (), None,
        )
        rendered = app.RedactingFormatter("%(message)s").format(record)
        self.assertNotIn(token, rendered)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ", rendered)
        self.assertIn("<BOT_TOKEN>", rendered)


if __name__ == "__main__":
    unittest.main()
