"""Offline regression tests for /accounts and removal of the About menu.
No Telegram installation, credentials or network requests are required.
"""
import ast
import asyncio
import html
from pathlib import Path
from types import SimpleNamespace

source = Path(__file__).with_name('bot.py').read_text(encoding='utf-8')
tree = ast.parse(source)
names = {
    'accounts_view', 'accounts_menu', 'accounts_command',
    'main_kb', 'help_command', 'callback',
}
selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
assert len(selected) == len(names), 'Missing a function required for account commands'

main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
def handler_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'localized_handler'
            and node.args and isinstance(node.args[0], ast.Name)):
        return node.args[0].id
    return None

handlers = {
    node.args[0].value: handler_name(node.args[1])
    for node in ast.walk(main)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    and node.func.id == 'CommandHandler' and len(node.args) >= 2
    and isinstance(node.args[0], ast.Constant)
}
assert handlers['accounts'] == 'accounts_command'
assert 'about' not in handlers
assert handlers['help'] == 'help_command'

class Button:
    def __init__(self, text, *args, callback_data=None, url=None, **kw):
        self.text, self.callback_data, self.url = text, callback_data, url
        if callback_data:
            assert len(callback_data.encode('utf-8')) <= 64

class Markup:
    def __init__(self, keyboard): self.inline_keyboard = keyboard

class Message:
    def __init__(self): self.sent = []
    async def reply_text(self, message, **kw):
        self.sent.append((message, kw))

class Query:
    def __init__(self, data=''):
        self.data, self.messages, self.answers = data, [], []
    async def answer(self, *args, **kw): self.answers.append((args, kw))
    async def edit_message_text(self, message, **kw): self.messages.append((message, kw))

accounts = [{'id': 1, 'label': '<my-account>', 'account_id': 'a'*32, 'active': 1}]
access = {'allowed': True, 'denied': 0}
async def deny(update): access['denied'] += 1
async def safe_edit(query, message, **kw): query.messages.append((message, kw))
async def ignored(*args, **kw): return None
async def localized_reply(msg, text, **kw): return await msg.reply_text(text, **kw)
async def localized_answer(q, text=None, **kw): return await q.answer(text, **kw)

ns = {
    'list_accounts': lambda: accounts,
    'InlineKeyboardButton': Button, 'InlineKeyboardMarkup': Markup,
    'esc': html.escape, 'short': lambda text, n=38: str(text)[:n],
    'ParseMode': SimpleNamespace(HTML='HTML'), 'VERSION': '2.7.2',
    'Update': object, 'ContextTypes': SimpleNamespace(DEFAULT_TYPE=object),
    'is_admin': lambda update: access['allowed'],
    'deny': deny, 'safe_edit': safe_edit,
    'localized_reply':localized_reply, 'localized_answer':localized_answer,
    'decode_callback_data': lambda s: s,
    'is_private_chat': lambda update: True,
    'active_account': lambda: accounts[0] if accounts else None,
    'log': SimpleNamespace(exception=lambda *args, **kw: None),
    'show_main': ignored, 'guide_menu': ignored,
}
exec(compile(ast.Module(body=selected, type_ignores=[]), 'bot_v271_extract.py', 'exec'), ns)

async def run():
    msg = Message()
    update = SimpleNamespace(effective_message=msg)
    context = SimpleNamespace(user_data={'state': 'editing'})
    await ns['accounts_command'](update, context)
    assert not context.user_data
    text, kw = msg.sent[-1]
    assert '<b>حساب‌های Cloudflare</b>' in text
    assert '&lt;my-account&gt;' in text
    assert kw['parse_mode'] == 'HTML'
    assert any(btn.callback_data == 'acc:view:1' for row in kw['reply_markup'].inline_keyboard for btn in row)
    assert any(btn.callback_data == 'acc:add' for row in kw['reply_markup'].inline_keyboard for btn in row)

    q = Query()
    await ns['accounts_menu'](q)
    assert q.messages[-1][0] == text  # /accounts and inline button share the same UI

    accounts.clear()
    await ns['accounts_command'](update, context)
    assert 'هنوز حسابی متصل نشده' in msg.sent[-1][0]
    accounts.append({'id': 1, 'label': '<my-account>', 'account_id': 'a'*32, 'active': 1})

    for connected in (True, False):
        buttons = ns['main_kb'](connected).inline_keyboard
        assert all(btn.callback_data != 'about' for row in buttons for btn in row)

    await ns['help_command'](update, context)
    help_text = msg.sent[-1][0]
    assert '/accounts' in help_text and '/about' not in help_text

    access['allowed'] = False
    old_count = len(msg.sent)
    await ns['accounts_command'](update, context)
    assert len(msg.sent) == old_count and access['denied'] == 1

asyncio.run(run())
print('PASS: /accounts, shared menus, About removal and admin checks')
