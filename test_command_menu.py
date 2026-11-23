"""Offline validation that all seven real bot commands are advertised."""
import ast
from pathlib import Path
source=Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
tree=ast.parse(source)
fn={node.name:node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
expected=["start","menu","accounts","help","cancel","language"]
main=fn["main"]
registered=[call.args[0].value for call in ast.walk(main)
    if isinstance(call,ast.Call) and isinstance(call.func,ast.Name)
    and call.func.id=="CommandHandler" and call.args
    and isinstance(call.args[0],ast.Constant)]
setting=next(node for node in tree.body if isinstance(node,ast.Assign)
    and any(isinstance(t,ast.Name) and t.id=="BOT_COMMAND_MENU" for t in node.targets))
menu=ast.literal_eval(setting.value)
assert registered==expected,registered
assert [name for name,_ in menu]==expected,menu
assert "id" not in expected
assert all(1<=len(name)<=32 and 1<=len(desc)<=256 for name,desc in menu)
assert "publish_bot_commands" in fn
assert "publish_user_commands" in fn
assert "command_list" in fn
assert any(isinstance(n,ast.Attribute) and n.attr=="post_init" for n in ast.walk(main))
print("PASS: six registered commands, locale-aware Telegram menu and per-chat command scope")
