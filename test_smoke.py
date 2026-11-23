"""End-to-end offline smoke test using the actual Telegram library and bot module."""
import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from cryptography.fernet import Fernet

with tempfile.TemporaryDirectory() as td:
    os.environ["DATA_DIR"] = td
    os.environ["TELEGRAM_BOT_TOKEN"] = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefg"
    os.environ["TELEGRAM_ADMIN_IDS"] = "11,22"
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    import bot

    bot.init_db()
    assert bot.VERSION == "1.0.0"
    assert bot.get_user_language(11) == "fa"
    bot.save_user_language(11, "en")
    assert bot.get_user_language(11) == "en"
    assert bot.get_user_language(22) == "fa"

    class Message:
        def __init__(self):
            self.sent=[]
        async def reply_text(self, text, **kw):
            self.sent.append((text,kw))

    class Query:
        def __init__(self, data):
            self.data, self.answers, self.edits=data, [], []
        async def answer(self, *args, **kw):
            self.answers.append((args, kw))
        async def edit_message_text(self, text, **kw):
            self.edits.append((text, kw))

    class FakeBot:
        def __init__(self):
            self.published=[]
        async def set_my_commands(self, commands, **kwargs):
            self.published.append((commands,kwargs))

    async def run():
        fake_bot=FakeBot()
        context=SimpleNamespace(user_data={"state":"example"}, bot=fake_bot)
        query=Query("lang:ru")
        update=SimpleNamespace(
            effective_user=SimpleNamespace(id=11),
            effective_chat=SimpleNamespace(id=11,type="private"),
            callback_query=query,
        )
        await bot.localized_handler(bot.callback)(update, context)
        assert bot.get_user_language(11) == "ru"
        assert "Язык бота" in query.edits[-1][0]
        assert context.user_data=={}
        assert fake_bot.published, "User's localized commands were not published"
        assert query.answers
        assert "lang:en" in str(query.edits[-1][1]["reply_markup"].inline_keyboard)
        buttons = query.edits[-1][1]["reply_markup"].inline_keyboard
        assert [b.callback_data for b in buttons[0]] == ["lang:en", "lang:fa", "lang:ru"]
        assert len(buttons) == 2 and len(buttons[1]) == 1
        assert buttons[1][0].callback_data == "main"

        message=Message()
        start_update=SimpleNamespace(
            effective_user=SimpleNamespace(id=11),
            effective_message=message,
        )
        await bot.localized_handler(bot.start)(start_update,SimpleNamespace(user_data={}))
        text,kw=message.sent[-1]
        assert "Аккаунты не подключены" in text
        labels=[b.text for row in kw["reply_markup"].inline_keyboard for b in row]
        assert any("Выбрать язык" in label for label in labels)

        # A different administrator still sees Persian.
        message2=Message()
        u2=SimpleNamespace(effective_user=SimpleNamespace(id=22),
                           effective_message=message2)
        await bot.localized_handler(bot.start)(u2,SimpleNamespace(user_data={}))
        assert "حسابی متصل نیست" in message2.sent[-1][0]
        assert bot.get_user_language(11)=="ru"

        # Changing back to English works without replacing the saved credentials.
        context.user_data={}
        query2=Query("lang:en")
        update.callback_query=query2
        await bot.localized_handler(bot.callback)(update,context)
        assert bot.get_user_language(11)=="en"
        assert "Bot language" in query2.edits[-1][0]
        assert "Cloudflare accounts" in bot.translate_text("👤 حساب‌های Cloudflare","en")

    asyncio.run(run())

print("PASS: actual bot imports, SQLite migration, language changes, translated menus and independent admins")
