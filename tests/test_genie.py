"""tests/test_genie.py

TDD tests for the Genie conversation proxy.

All SDK calls are mocked -- no Databricks workspace needed.
Tests cover:
  - genie.ask()  with a new conversation (start_conversation)
  - genie.ask()  with an existing conversation (create_message)
  - genie.poll() with a COMPLETED message + text attachment
  - genie.poll() with an in-progress message (no attachments)
  - POST /api/genie/ask  happy path and 503 guard
  - GET  /api/genie/poll happy path and 503 guard
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings

_client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_ws_new_conversation() -> tuple[MagicMock, MagicMock]:
    """Return (mock_ws, mock_wait) pre-wired for start_conversation."""
    mock_ws = MagicMock()
    mock_wait = MagicMock()
    mock_wait.response.conversation.id = "conv-abc"
    mock_wait.response.message.id = "msg-111"
    mock_ws.genie.start_conversation.return_value = mock_wait
    return mock_ws, mock_wait


def _mock_ws_existing_conversation() -> tuple[MagicMock, MagicMock]:
    """Return (mock_ws, mock_wait) pre-wired for create_message."""
    mock_ws = MagicMock()
    mock_wait = MagicMock()
    mock_wait.response.id = "msg-222"
    mock_ws.genie.create_message.return_value = mock_wait
    return mock_ws, mock_wait


def _mock_ws_completed_message() -> MagicMock:
    """Return a mock_ws whose get_message returns a COMPLETED message with text."""
    from databricks.sdk.service.dashboards import MessageStatus

    mock_ws = MagicMock()
    mock_msg = MagicMock()
    mock_msg.status = MessageStatus.COMPLETED
    mock_att = MagicMock()
    mock_att.text.content = "Total production is 15,400 units across 11 SKUs."
    mock_att.query = None  # no SQL attachment
    mock_msg.attachments = [mock_att]
    mock_ws.genie.get_message.return_value = mock_msg
    return mock_ws


# ---------------------------------------------------------------------------
# Unit tests: genie.ask()
# ---------------------------------------------------------------------------

class TestGenieAskUnit:
    def test_new_conversation_returns_ids(self, monkeypatch):
        """ask() with no conversation_id calls start_conversation and returns IDs."""
        import backend.genie as genie_module
        mock_ws, _ = _mock_ws_new_conversation()
        monkeypatch.setattr(genie_module, "_ws_client", mock_ws)

        result = genie_module.ask("space-xyz", "What is total production?")

        assert result == {"conversation_id": "conv-abc", "message_id": "msg-111"}
        mock_ws.genie.start_conversation.assert_called_once_with(
            space_id="space-xyz", content="What is total production?"
        )

    def test_existing_conversation_calls_create_message(self, monkeypatch):
        """ask() with a conversation_id calls create_message and returns IDs."""
        import backend.genie as genie_module
        mock_ws, _ = _mock_ws_existing_conversation()
        monkeypatch.setattr(genie_module, "_ws_client", mock_ws)

        result = genie_module.ask("space-xyz", "Break that down by SKU", conversation_id="conv-abc")

        assert result == {"conversation_id": "conv-abc", "message_id": "msg-222"}
        mock_ws.genie.create_message.assert_called_once_with(
            space_id="space-xyz",
            conversation_id="conv-abc",
            content="Break that down by SKU",
        )


# ---------------------------------------------------------------------------
# Unit tests: genie.poll()
# ---------------------------------------------------------------------------

class TestGeniePollUnit:
    def test_completed_message_returns_status_and_text(self, monkeypatch):
        """poll() extracts status and text from a COMPLETED message."""
        import backend.genie as genie_module
        mock_ws = _mock_ws_completed_message()
        monkeypatch.setattr(genie_module, "_ws_client", mock_ws)

        result = genie_module.poll("space-xyz", "conv-abc", "msg-111")

        assert result["status"] == "COMPLETED"
        assert result["text"] == "Total production is 15,400 units across 11 SKUs."
        assert "sql" not in result
        mock_ws.genie.get_message.assert_called_once_with(
            space_id="space-xyz",
            conversation_id="conv-abc",
            message_id="msg-111",
        )

    def test_completed_message_with_sql_attachment(self, monkeypatch):
        """poll() includes sql key when a query attachment is present."""
        from databricks.sdk.service.dashboards import MessageStatus
        import backend.genie as genie_module

        mock_ws = MagicMock()
        mock_msg = MagicMock()
        mock_msg.status = MessageStatus.COMPLETED

        text_att = MagicMock()
        text_att.text.content = "Here is the breakdown:"
        text_att.query = None

        query_att = MagicMock()
        query_att.text = None
        query_att.query.query = "SELECT sku_code, SUM(planned_qty) FROM plan_line GROUP BY 1"

        mock_msg.attachments = [text_att, query_att]
        mock_ws.genie.get_message.return_value = mock_msg
        monkeypatch.setattr(genie_module, "_ws_client", mock_ws)

        result = genie_module.poll("space-xyz", "conv-abc", "msg-111")

        assert result["status"] == "COMPLETED"
        assert result["text"] == "Here is the breakdown:"
        assert "SELECT" in result["sql"]

    def test_in_progress_message_no_text(self, monkeypatch):
        """poll() returns status with text=None for an in-progress message."""
        from databricks.sdk.service.dashboards import MessageStatus
        import backend.genie as genie_module

        mock_ws = MagicMock()
        mock_msg = MagicMock()
        mock_msg.status = MessageStatus.ASKING_AI
        mock_msg.attachments = []
        mock_ws.genie.get_message.return_value = mock_msg
        monkeypatch.setattr(genie_module, "_ws_client", mock_ws)

        result = genie_module.poll("space-xyz", "conv-abc", "msg-111")

        assert result["status"] == "ASKING_AI"
        assert result["text"] is None
        assert "sql" not in result


# ---------------------------------------------------------------------------
# Endpoint tests: POST /api/genie/ask
# ---------------------------------------------------------------------------

class TestGenieAskEndpoint:
    def test_503_when_not_configured(self):
        """Returns 503 when PET_GENIE_SPACE_ID is unset (default empty string)."""
        # settings.genie_space_id defaults to "" in the test environment
        r = _client.post("/api/genie/ask", json={"question": "test"})
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"].lower()

    def test_returns_conversation_and_message_ids(self, monkeypatch):
        """Returns {conversation_id, message_id} from a mocked start_conversation."""
        import backend.genie as genie_module
        monkeypatch.setattr(settings, "genie_space_id", "space-xyz")
        mock_ws, _ = _mock_ws_new_conversation()
        monkeypatch.setattr(genie_module, "_ws_client", mock_ws)

        r = _client.post("/api/genie/ask", json={"question": "What is total production?"})

        assert r.status_code == 200
        data = r.json()
        assert data["conversation_id"] == "conv-abc"
        assert data["message_id"] == "msg-111"

    def test_passes_conversation_id_when_provided(self, monkeypatch):
        """Passes conversation_id to genie.ask when present in body."""
        import backend.genie as genie_module
        monkeypatch.setattr(settings, "genie_space_id", "space-xyz")
        mock_ws, _ = _mock_ws_existing_conversation()
        monkeypatch.setattr(genie_module, "_ws_client", mock_ws)

        r = _client.post(
            "/api/genie/ask",
            json={"question": "Break that down", "conversation_id": "conv-abc"},
        )

        assert r.status_code == 200
        data = r.json()
        assert data["conversation_id"] == "conv-abc"
        assert data["message_id"] == "msg-222"


# ---------------------------------------------------------------------------
# Endpoint tests: GET /api/genie/poll
# ---------------------------------------------------------------------------

class TestGeniePollEndpoint:
    def test_503_when_not_configured(self):
        """Returns 503 when PET_GENIE_SPACE_ID is unset."""
        r = _client.get(
            "/api/genie/poll",
            params={"conversation_id": "conv-abc", "message_id": "msg-111"},
        )
        assert r.status_code == 503

    def test_returns_status_and_text(self, monkeypatch):
        """Returns {status, text} for a COMPLETED message."""
        import backend.genie as genie_module
        monkeypatch.setattr(settings, "genie_space_id", "space-xyz")
        mock_ws = _mock_ws_completed_message()
        monkeypatch.setattr(genie_module, "_ws_client", mock_ws)

        r = _client.get(
            "/api/genie/poll",
            params={"conversation_id": "conv-abc", "message_id": "msg-111"},
        )

        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "COMPLETED"
        assert "15,400 units" in data["text"]
