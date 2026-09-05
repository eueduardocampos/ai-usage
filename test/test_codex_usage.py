# -*- coding: utf-8 -*-
"""Garante que a importacao do Claude nao contamina o consumo do ChatGPT."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import codex_usage


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


with tempfile.TemporaryDirectory() as tmp:
    sessions = os.path.join(tmp, "sessions")
    os.makedirs(sessions)
    imports = os.path.join(tmp, "imports.json")
    path = os.path.join(sessions, "importada.jsonl")
    sid = "sessao-importada"

    with open(imports, "w", encoding="utf-8") as f:
        json.dump({"records": [{"imported_thread_id": sid}]}, f)

    write_jsonl(path, [
        {"timestamp": "2026-09-05T10:00:00Z", "type": "session_meta",
         "payload": {"session_id": sid, "model_provider": "openai"}},
        # Contador sintetico que veio do Claude: deve ser ignorado.
        {"timestamp": "2026-09-05T10:00:01Z", "type": "event_msg",
         "payload": {"type": "token_count", "info": {
             "last_token_usage": {"total_tokens": 999999,
                                  "input_tokens": 0, "output_tokens": 0}}}},
        # Uso novo depois que a conversa foi retomada no Codex.
        {"timestamp": "2026-09-05T10:01:00Z", "type": "turn_context",
         "payload": {"model": "gpt-teste"}},
        {"timestamp": "2026-09-05T10:01:01Z", "type": "event_msg",
         "payload": {"type": "token_count", "info": {
             "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 80,
                                  "output_tokens": 20, "reasoning_output_tokens": 5}},
             "rate_limits": {"limit_id": "codex", "plan_type": "teste",
                             "primary": {"used_percent": 12,
                                         "window_minutes": 300,
                                         "resets_at": 1788627176}}}},
    ])

    old_glob, old_imports = codex_usage.SESSIONS_GLOB, codex_usage.IMPORTS_PATH
    codex_usage.SESSIONS_GLOB = os.path.join(sessions, "*.jsonl")
    codex_usage.IMPORTS_PATH = imports
    try:
        collector = codex_usage.CodexUsage()
        collector.scan()
        state = collector.state()
    finally:
        codex_usage.SESSIONS_GLOB, codex_usage.IMPORTS_PATH = old_glob, old_imports

    assert state["history"]["geral"]["total_tokens"] == 120
    assert state["history"]["geral"]["cached_input_tokens"] == 80
    assert state["history"]["geral"]["calls"] == 1
    assert state["resumed_imported_sessions"] == 1
    assert state["ignored_imported_sessions"] == 0
    assert state["limits"][0]["primary"]["used_percent"] == 12

print("TUDO PASSOU")
