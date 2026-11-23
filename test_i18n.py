"""Offline localization regression tests (no Telegram tokens or network)."""
import ast
import asyncio
import re
import sqlite3
import tempfile
from contextlib import closing
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

from i18n import CATALOG, LANGUAGES, translate_text

source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
tree = ast.parse(source)
fn = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
assert all(name in fn for name in (
    "tr", "get_user_language", "save_user_language", "localized_handler",
    "language_view", "language_command", "show_language_menu", "command_list",
    "publish_user_commands", "publish_bot_commands", "main_kb", "InlineKeyboardButton",
))
# The seven commands actually handled must match Telegram command menu.
registered = [call.args[0].value for call in ast.walk(fn["main"])
              if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
              and call.func.id == "CommandHandler" and isinstance(call.args[0], ast.Constant)]
expected = ["start", "menu", "accounts", "help", "cancel", "language"]
assert registered == expected, registered
assert 'callback_data="language"' in source
assert 'd.startswith("lang:")' in source
assert 'CREATE TABLE IF NOT EXISTS user_languages' in source
assert 'CURRENT_LANGUAGE.reset(token)' in source
assert 'tr(text)' in source

assert len(CATALOG) >= 250, len(CATALOG)
for fa_text, langs in CATALOG.items():
    for lang in ("en", "ru"):
        assert lang in langs, (fa_text[:90], lang)
        translated = translate_text(fa_text, lang)
        assert translated != fa_text or fa_text == "🇮🇷 فارسی", (lang, fa_text[:100])
        assert not re.search(r"[\u0600-\u06ff]", translated) or fa_text == "🇮🇷 فارسی", (lang, translated)
        assert re.findall(r"</?(?:b|code|i|a)\b[^>]*>", fa_text) == re.findall(
            r"</?(?:b|code|i|a)\b[^>]*>", translated
        ), (lang, fa_text[:100])
assert translate_text("سلام", "fa") == "سلام"
assert translate_text("foo", "ru") == "foo"
assert translate_text("DNS example.com", "en") == "DNS example.com"

home = ("☁️ <b>Cloudflare Manager</b>  |  نسخه 2.8.0\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "🌐 از منوی زیر بخش موردنظر را انتخاب کنید. برای بررسی اعتبار توکن، از «حساب‌های Cloudflare ← تست اتصال» استفاده کنید.")
for lang in ("en", "ru"):
    text = translate_text(home, lang)
    assert "<b>" in text and "</b>" in text
    assert "حساب" not in text

with tempfile.TemporaryDirectory() as td:
    path = Path(td) / 'bot.db'
    def db_connect():
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        return con
    with closing(db_connect()) as con:
        con.execute("CREATE TABLE user_languages (user_id INTEGER PRIMARY KEY, "
                    "language TEXT NOT NULL CHECK(language IN ('fa','en','ru')))")
        con.commit()

    ctx = ContextVar('test_language', default='fa')
    class Button:
        def __init__(self, text, *args, callback_data=None, url=None, **kw):
            self.text, self.callback_data, self.url = text, callback_data, url
    class Keyboard:
        def __init__(self, rows): self.inline_keyboard = rows
    class Command:
        def __init__(self, command, description):
            self.command, self.description = command, description
    class PrivateScope: pass
    class ChatScope:
        def __init__(self, chat_id): self.chat_id = chat_id

    language_names = {"fa": "🇮🇷 فارسی", "en": "🇬🇧 English", "ru": "🇷🇺 Русский"}
    ns = {
        'closing': closing, 'db_connect': db_connect, 'LANGUAGES': LANGUAGES,
        'CURRENT_LANGUAGE': ctx, 'wraps': lambda f: (lambda x:x),
        'translate_text': translate_text, 'tr': lambda x: translate_text(x, ctx.get()),
        'LANGUAGE_NAMES': language_names,
        'InlineKeyboardMarkup': Keyboard, 'InlineKeyboardButton': Button,
        'BotCommand': Command, 'BotCommandScopeChat': ChatScope,
        'BotCommandScopeAllPrivateChats': PrivateScope,
        'ParseMode': SimpleNamespace(HTML='HTML'), 'VERSION':'2.8.1',
        'Update': object, 'Application': object, 'ContextTypes':SimpleNamespace(DEFAULT_TYPE=object),
        'is_admin':lambda update:True,
        'localized_reply':None,
        'log':SimpleNamespace(warning=lambda *args, **kw: None),
    }
    menu = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'BOT_COMMAND_MENU' for t in n.targets))
    ns['BOT_COMMAND_MENU']=ast.literal_eval(menu.value)
    names = ['get_user_language','save_user_language','localized_handler',
             'language_view','command_list','publish_user_commands','publish_bot_commands']
    extracted=[fn[n] for n in names]
    exec(compile(ast.Module(body=extracted,type_ignores=[]),'bot_localization.py','exec'),ns)
    assert ns['get_user_language'](100) == 'fa'
    ns['save_user_language'](100,'ru')
    ns['save_user_language'](200,'en')
    assert ns['get_user_language'](100) == 'ru'
    assert ns['get_user_language'](200) == 'en'
    ns['save_user_language'](100,'fa')
    assert ns['get_user_language'](100) == 'fa'
    try:
        ns['save_user_language'](100,'de')
    except ValueError: pass
    else: raise AssertionError('Invalid language accepted')
    assert ns['get_user_language'](100) == 'fa'
    ns['save_user_language'](100,'ru')
    token=ctx.set('ru')
    text,keyboard=ns['language_view'](True)
    assert 'успешно изменён' in text
    assert len(keyboard.inline_keyboard) == 2
    assert [b.callback_data for b in keyboard.inline_keyboard[0]] == [
        "lang:en", "lang:fa", "lang:ru"
    ], "Language buttons must share one row, with Persian in the middle"
    assert len(keyboard.inline_keyboard[1]) == 1
    assert keyboard.inline_keyboard[1][0].callback_data == "main"
    assert any(b.callback_data=='lang:fa' for row in keyboard.inline_keyboard for b in row)
    assert any(b.callback_data=='lang:en' for row in keyboard.inline_keyboard for b in row)
    assert any(b.callback_data=='lang:ru' for row in keyboard.inline_keyboard for b in row)
    assert any('✅ 🇷🇺' in b.text for row in keyboard.inline_keyboard for b in row)
    assert all(len(b.text.encode()) <= 64 for row in keyboard.inline_keyboard for b in row)
    ctx.reset(token)

    async def exercise():
        async def response(update, context):
            await asyncio.sleep(0)
            return ns['get_user_language'](update.effective_user.id),ctx.get()
        wrapped = ns['localized_handler'](response)
        u = lambda i:SimpleNamespace(effective_user=SimpleNamespace(id=i))
        answers=await asyncio.gather(wrapped(u(100),None),wrapped(u(200),None))
        assert answers == [('ru','ru'),('en','en')],answers
        assert ctx.get() == 'fa',ctx.get()
        calls=[]
        class Bot:
            async def set_my_commands(self, commands, **kwargs):
                calls.append((commands,kwargs))
        bot=Bot()
        await ns['publish_bot_commands'](SimpleNamespace(bot=bot))
        assert len(calls)==4,len(calls)
        assert calls[2][1]['language_code']=='en'
        assert calls[3][1]['language_code']=='ru'
        assert [c.command for c in calls[0][0]] == expected
        assert 'Главное меню' in calls[3][0][0].description
        calls.clear()
        await ns['publish_user_commands'](bot,12345,'en')
        assert len(calls)==1 and calls[0][1]['scope'].chat_id==12345
        assert 'Main menu' in calls[0][0][0].description
    asyncio.run(exercise())
print('PASS: full phrase coverage, HTML, per-user persistent language, picker, commands and concurrent isolation')
