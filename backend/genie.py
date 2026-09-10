"""backend/genie.py

Genie conversation proxy for the Lactalis PET Line Planner.

Wraps the Databricks SDK genie API so the frontend chat panel can ask
questions and poll for answers without blocking the event loop.

SDK methods used (confirmed from databricks.sdk.service.dashboards):
  - w.genie.start_conversation(space_id, content) -> Wait[GenieMessage]
      Response shape: wait.response is GenieStartConversationResponse with
      .conversation.id (conversation_id) and .message.id (message_id).
  - w.genie.create_message(space_id, conversation_id, content) -> Wait[GenieMessage]
      Response shape: wait.response is GenieMessage with .id (message_id).
  - w.genie.get_message(space_id, conversation_id, message_id) -> GenieMessage
      GenieMessage has .status (MessageStatus enum), .attachments
      (list of GenieAttachment with .text.content and .query.query).

Auth strategy (dual-mode, same pattern as db.py):
  - Deployed on Databricks Apps: WorkspaceClient() resolves injected SP
    environment variables automatically.
  - Local dev: set DATABRICKS_CONFIG_PROFILE before running the server.

WorkspaceClient is constructed lazily on first call to _client() so
importing this module never triggers a network connection.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ws_client = None


def _client():
    """Return a cached WorkspaceClient, constructing it on first call."""
    global _ws_client
    if _ws_client is None:
        from databricks.sdk import WorkspaceClient
        _ws_client = WorkspaceClient()
    return _ws_client


def ask(space_id: str, question: str, conversation_id: str | None = None) -> dict:
    """Start a new Genie conversation or post a follow-up to an existing one.

    When conversation_id is None, calls start_conversation and returns the
    new conversation_id together with the first message_id.

    When conversation_id is provided, calls create_message and returns the
    same conversation_id together with the new message_id.

    Both SDK methods return a Wait object immediately (no blocking poll here
    -- the frontend polls separately via genie.poll()).

    Returns:
        {conversation_id: str, message_id: str}
    """
    w = _client()
    if conversation_id is None:
        wait = w.genie.start_conversation(space_id=space_id, content=question)
        # wait.response is GenieStartConversationResponse
        conv_id = wait.response.conversation.id
        msg_id = wait.response.message.id
    else:
        wait = w.genie.create_message(
            space_id=space_id,
            conversation_id=conversation_id,
            content=question,
        )
        # wait.response is GenieMessage
        conv_id = conversation_id
        msg_id = wait.response.id
    return {"conversation_id": conv_id, "message_id": msg_id}


def poll(space_id: str, conversation_id: str, message_id: str) -> dict:
    """Fetch the current state of a Genie message.

    Calls get_message and returns status and any available text. When a
    query attachment is present, the generated SQL is also included.

    Returns:
        {status: str, text: str | None}  -- plus optional {sql: str}
    """
    w = _client()
    msg = w.genie.get_message(
        space_id=space_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    status = msg.status.value if msg.status else None
    text: str | None = None
    sql: str | None = None
    for att in (msg.attachments or []):
        if att.text and att.text.content and text is None:
            text = att.text.content
        if att.query and att.query.query and sql is None:
            sql = att.query.query
    result: dict = {"status": status, "text": text}
    if sql is not None:
        result["sql"] = sql
    return result
