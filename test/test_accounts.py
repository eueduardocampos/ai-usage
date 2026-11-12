# -*- coding: utf-8 -*-
"""Varias contas: cadastro, isolamento dos snapshots por conta e login pendente."""
import os, sys, tempfile, types
sys.path.insert(0, os.getcwd())
import accounts, auth, store as store_mod

falhas = []
def check(cond, label):
    print(("  PASS  " if cond else "  FALHA ") + label)
    if not cond: falhas.append(label)

tmp = tempfile.mkdtemp()
principal = types.SimpleNamespace(token_store=auth.TokenStore(tmp), id="principal")
am = accounts.AccountManager(tmp, tmp, principal)
check([a["id"] for a in am.accounts] == ["principal", "codex"], "padrao: principal + codex")

nova = am.create("claude", "  Conta   do cliente  ")
check(nova["name"] == "Conta do cliente", "nome limpo")
check([a["id"] for a in am.accounts] == ["principal", nova["id"], "codex"], "nova conta entra antes do Codex")
rt = am.runtime(nova["id"])
check(rt.token_store.allow_bootstrap is False, "conta extra nao herda o login do Claude Code")
check(rt.token_store.path != principal.token_store.path, "token proprio por conta")

am2 = accounts.AccountManager(tmp, tmp, principal)
check([a["name"] for a in am2.accounts] == ["Claude 1", "Conta do cliente", "ChatGPT"], "persiste em accounts.json")

try:
    am.remove("principal"); check(False, "principal nao pode ser removida")
except ValueError:
    check(True, "principal nao pode ser removida")
try:
    am.update(nova["id"], {"app": "/bin/ls"}); check(False, "app fora da lista e recusado")
except ValueError:
    check(True, "app fora da lista e recusado")

# snapshots separados por conta
st = store_mod.Store(os.path.join(tmp, "painel.db"))
st.insert_snapshot({"windows": {"five_hour": {"utilization": 10, "resets_at": None}}, "extra_usage": None})
st.insert_snapshot({"windows": {"five_hour": {"utilization": 77, "resets_at": None}}, "extra_usage": None}, account=nova["id"])
check(st.latest_state()["windows"]["five_hour"]["utilization"] == 10, "principal le o proprio snapshot")
check(st.latest_state(account=nova["id"])["windows"]["five_hour"]["utilization"] == 77, "conta extra le o proprio snapshot")
check(st.latest_state(account="nao-existe") is None, "conta sem leitura = None")

# login: link gerado, state amarrado a conta
lb = accounts.LoginBroker(0, lambda aid: None)
lb._ensure_server = lambda: None
url = lb.start(nova["id"], rt.token_store)
check("code_challenge=" in url and "state=" in url, "link de login com PKCE")
check(lb.state_of(nova["id"])["state"] == "waiting", "conta fica aguardando login")
ok, _ = lb._finish("state-falso", "x", None)
check(ok is False, "state desconhecido e recusado")

am.remove(nova["id"])
check(am.get(nova["id"]) is None, "remover conta extra")

print()
print(("%d FALHA(S)" % len(falhas)) if falhas else "TUDO PASSOU")
sys.exit(1 if falhas else 0)
