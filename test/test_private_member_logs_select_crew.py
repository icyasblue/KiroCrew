"""Private-member fix for kiro_cli_logs.

A private member's identity resolves through code that READS the member's
transcript, which the "Private member view" sandbox hides, so that read fails.
The fix resolves the member through its verified MCP authority
(mcp_memory_scope) and, when that authority cannot read the hidden transcript,
from the readable binding, so the tool returns the intended "unavailable to
private members" refusal.
"""

from __future__ import annotations

import unittest.mock

import kiro_crew.mcp_core as mcp_core


class TestKiroCliLogsPrivateMember:
    def _bind_member(self, home, monkeypatch, *, session="dashboard:alice", store="member-alice"):
        from member_memory_helpers import forget_declared_stores, write_member_home

        from kiro_crew import memory_stores
        from kiro_crew.vector_memory import VectorMemoryStore

        monkeypatch.setenv("KIROCREW_HOME", str(home))
        write_member_home(home, store.removeprefix("member-"))
        forget_declared_stores(monkeypatch)
        tier = VectorMemoryStore(
            db_path=home / "memory_stores" / store / memory_stores.MEMORY_DB_FILE
        )
        tier.init()
        self._tier = tier
        from kiro_crew.member_memory_auth import bind_private_session_store

        bind_private_session_store(session, store)
        monkeypatch.setattr(mcp_core, "_resolve_session_key_strict", lambda: session)

    def test_private_member_gets_accurate_refusal_not_identity_error(self, tmp_path, monkeypatch):
        # A private member (verified binding, transcripts hidden) must get the
        # INTENDED "unavailable to private members" refusal — resolved from the
        # member's verified MCP authority (mcp_memory_scope), not the lenient
        # session key, and audited denied_memory_scope.
        self._bind_member(tmp_path, monkeypatch)
        # No stub on mcp_memory_scope: with the transcript hidden it raises, as
        # it does inside a real member sandbox, and the readable binding alone
        # selects the private-member refusal.
        captured: dict = {}

        class _Sel:
            def log_tool_invocation(self, **kw):
                captured.update(kw)

        monkeypatch.setattr(mcp_core, "sel", lambda: _Sel())
        out = mcp_core._call_tool_inner("kiro_cli_logs", {})
        assert out == "Error: shared kiro-cli logs are unavailable to private members."
        # And the audit outcome is the deliberate one, not the error fallthrough.
        assert captured.get("outcome") == "denied_memory_scope"

    def test_global_v1_caller_reads_logs(self, tmp_path, monkeypatch):
        # No binding, no boundaries -> the original Global caller contract: the
        # tool proceeds to read logs (stubbed here) rather than refusing.
        monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
        (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
        from kiro_crew.mcp_tools import logs as logs_tool

        monkeypatch.setattr(
            logs_tool.diagnostics, "read_kiro_cli_logs", lambda tail=None, since=None: "LOG BODY"
        )
        monkeypatch.setattr(mcp_core, "sel", lambda: unittest.mock.MagicMock())
        out = mcp_core._call_tool_inner("kiro_cli_logs", {})
        assert "unavailable to private members" not in out
        assert "LOG BODY" in out
