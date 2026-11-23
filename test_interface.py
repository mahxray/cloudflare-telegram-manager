"""Offline UI, wizard privacy, pagination and operation-confirmation tests.

Run with: python3 test_interface.py. Does not import Telegram, use secrets or call the network.
"""
import ast
import asyncio
import base64
import html
import re
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

source=Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
tree=ast.parse(source)
names={
    "encode_callback_data", "decode_callback_data", "InlineKeyboardButton",
    "home_text", "main_kb", "page_navigation", "guide_menu",
    "account_detail_menu", "accounts_view", "accounts_menu", "fetch_all_zones", "list_records",
    "dnssec_toggle", "callback", "safe_edit", "is_private_chat",
}
selected=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
assert len(selected)==len(names)
created=[]
class TelegramInlineKeyboardButton:
    def __init__(self,text,*args,callback_data=None,url=None,**kwargs):
        self.text,self.callback_data,self.url=text,callback_data,url
        if callback_data is not None:
            assert 1<=len(callback_data.encode())<=64,callback_data
        created.append(self)
class InlineKeyboardMarkup:
    def __init__(self,keyboard): self.inline_keyboard=keyboard
class BadRequest(Exception): pass
class Query:
    def __init__(self,data=""):
        self.data=data; self.answers=[]; self.messages=[]
    async def answer(self,*args,**kwargs): self.answers.append((args,kwargs))
    async def edit_message_text(self,text,**kwargs): self.messages.append((text,kwargs))

Z='a'*32
R='b'*32
account={"id":1,"label":"آزمایشی <a>","account_id":'f'*32,"active":1,"token_enc":b'fake'}
commands=[]
async def cf_request(a,method,path,**kw):
    commands.append((method,path,kw))
    if path=="/zones":
        page=kw['params']['page']
        return {"result":[{"name":f"domain{page}-{i}.test","id":Z} for i in range(50 if page==1 else 3)],
                "result_info":{"total_pages":2,"total_count":53}}
    if '/dns_records' in path:
        return {"result":[{"id":R} for _ in range(500 if kw['params']['page']==1 else 2)],
                "result_info":{"total_pages":2,"total_count":502}}
    if path.endswith('/dnssec'):
        if method=="PATCH": return {"result":{"status":kw['json_body']['status']}}
        return {"result":{"status":"active"}}
    if method in ("POST","PATCH"):
        return {"result":{}}
    raise AssertionError((method,path,kw))
async def fake_zone(zid): return {"name":"example.test","paused":False,"status":"active"}
async def ignored(*args,**kwargs): return None
ns={'tr':lambda x:x, 'localized_answer':lambda q,text=None,**kw:q.answer(text,**kw),
    'base64':base64,'re':re,'_CF_HEX_ID':re.compile(r'(?<=:)[0-9a-fA-F]{32}(?=:|$)'),
    '_SHORT_CF_ID':re.compile(r'^~[A-Za-z0-9_-]{22}$'),
    'TelegramInlineKeyboardButton':TelegramInlineKeyboardButton,
    'InlineKeyboardMarkup':InlineKeyboardMarkup,'BadRequest':BadRequest,
    'VERSION':'2.7.2','Update':object,'ContextTypes':SimpleNamespace(DEFAULT_TYPE=object),
    'ParseMode':SimpleNamespace(HTML='HTML'),
    'list_accounts':lambda:[account], 'get_account':lambda aid:account if aid==1 else None,
    'active_account':lambda:account,'get_zone':fake_zone,
    'esc':lambda s:html.escape(str(s)), 'short':lambda s,n=38:str(s)[:n],
    'cf_request':cf_request,'is_admin':lambda update:True,
    'zone_menu':ignored,'zone_info':ignored,'dns_menu':ignored,'settings_menu':ignored,
    'cache_menu':ignored,'dnssec_menu':ignored,'zones_menu':ignored,
    'record_type_menu':ignored,'record_menu':ignored,'toggle_proxy':ignored,
    'toggle_setting':ignored,'export_dns':ignored,'toggle_zone_pause':ignored,
    'create_pending_record':ignored,'get_record':ignored,
    'log':SimpleNamespace(exception=lambda *x,**k:None),
}
exec(compile(ast.Module(body=selected,type_ignores=[]),'bot_v27_extract.py','exec'),ns)
ns.update(show_main=ignored, cancel_kb=ignored, deny=ignored, get_account=lambda aid:account)

async def run():
    q=Query()
    home = ns['home_text'](account)
    assert account['label'] not in home
    assert 'آزمایشی &lt;a&gt;' not in home
    assert 'حساب فعال' not in home and 'حساب ذخیره‌شده' not in home
    for c in (True,False):
        kb=ns['main_kb'](c)
        assert kb.inline_keyboard
    assert len(ns['page_navigation'](0,3,'zones'))==2
    assert len(ns['page_navigation'](1,3,f'dns:{Z}'))==3
    for section in ('start','token','id','finish','errors'):
        await ns['guide_menu'](q,section)
        assert 'Cloudflare' in q.messages[-1][0] or section!='start'
    await ns['accounts_menu'](q)
    await ns['account_detail_menu'](q,1)
    assert 'آزمایشی &lt;a&gt;' in q.messages[-1][0]
    assert 'domain1-0.test' in q.messages[-1][0]
    assert '53' in q.messages[-1][0]
    assert commands[-1][2]['params']['account.id'] == account['account_id']
    assert len(await ns['fetch_all_zones'](account))==53
    assert len(await ns['list_records'](Z))==502

    def upd(data,chat_type='private'):
        return SimpleNamespace(callback_query=Query(data),effective_chat=SimpleNamespace(type=chat_type))
    def ctx(): return SimpleNamespace(user_data={})

    second_account = dict(account, id=2, account_id='0'*32, active=0)
    ns['get_account'] = lambda aid: {1: account, 2: second_account}.get(aid)
    u = upd('acc:view:2:1')
    await ns['callback'](u, ctx())
    detail, kwargs = u.callback_query.messages[-1]
    assert 'domain2-0.test' in detail
    assert second_account['account_id'] in detail
    assert commands[-1][2]['params'] == {'account.id': second_account['account_id'], 'per_page': 10, 'page': 2}
    assert all(not (btn.callback_data or '').startswith('acc:select:')
               for row in kwargs['reply_markup'].inline_keyboard for btn in row)
    ns['list_accounts'] = lambda: [account, second_account]
    home_buttons = ns['main_kb'](True).inline_keyboard
    assert home_buttons[0][0].callback_data == 'zones:account'
    u = upd('zones:account')
    await ns['callback'](u, ctx())
    picker, picker_kwargs = u.callback_query.messages[-1]
    assert 'حساب Cloudflare را انتخاب کنید' in picker
    buttons = picker_kwargs['reply_markup'].inline_keyboard
    assert [row[0].callback_data for row in buttons[:2]] == ['zones:account:1', 'zones:account:2']

    def select_account(sql, params):
        assert params == (2,)
        account['active'], second_account['active'] = 0, 1

    async def show_selected_zones(q):
        assert second_account['active'] and not account['active']
        await q.edit_message_text(second_account['account_id'])

    ns.update(
        closing=nullcontext,
        db_connect=lambda: SimpleNamespace(execute=select_account, commit=lambda: None),
        zones_menu=show_selected_zones,
    )
    u = upd('zones:account:2')
    await ns['callback'](u, ctx())
    assert u.callback_query.messages[-1][0] == second_account['account_id']
    account['active'], second_account['active'] = 1, 0
    ns.update(list_accounts=lambda: [account], zones_menu=ignored)

    u=upd('acc:add','group'); context=ctx()
    await ns['callback'](u,context)
    assert not context.user_data
    assert any(item[1].get('show_alert') for item in u.callback_query.answers)
    u=upd('acc:add'); context=ctx()
    await ns['callback'](u,context)
    assert context.user_data['state']=='acc_label'
    u=upd('guide:token'); context=ctx()
    await ns['callback'](u,context)
    assert 'API Token' in u.callback_query.messages[-1][0]

    commands.clear()
    u=upd(f'purgeall:{Z}'); context=ctx()
    await ns['callback'](u,context)
    assert not any(m=='POST' for m,_,__ in commands),'purge before confirmation'
    u=upd(f'purgeconfirm:{Z}'); context=ctx()
    await ns['callback'](u,context)
    assert any(m=='POST' for m,_,__ in commands)

    commands.clear()
    u=upd(f'dnssectoggle:{Z}'); context=ctx()
    await ns['callback'](u,context)
    assert not any(m=='PATCH' for m,_,__ in commands)
    u=upd(f'dnssecapply:{Z}'); context=ctx()
    await ns['callback'](u,context)
    assert any(m=='PATCH' and kw['json_body']['status']=='disabled' for m,_,kw in commands)

asyncio.run(run())
print(f'PASS: current menus, escaped account labels, token privacy, pagination and destructive confirmations; {len(created)} buttons valid')
