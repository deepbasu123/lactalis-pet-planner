"""backend/genie.py

Genie conversation proxy for the Lactalis PET Line Planner.

Wraps the Databricks SDK genie API so the frontend chat panel can ask
questions and poll for answers without blocking the event loop.

SDK methods used -- all confirmed from installed SDK source:
  databricks.sdk.service.dashboards (GenieAPI class):

  w.genie.start_conversation(space_id, content) -> Wait[GenieMessage]
      POST /api/2.0/genie/spaces/{space_id}/start-conversation
      wait.response is GenieStartConversationResponse with
      .conversation.id (conversation_id) and .message.id (message_id).

  w.genie.create_message(space_id, conversation_id, content) -> Wait[GenieMessage]
      POST /api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages
      wait.response is GenieMessage; .id is the new message_id.

  w.genie.get_message(space_id, conversation_id, message_id) -> GenieMessage
      GET .../messages/{message_id}
      GenieMessage.status is MessageStatus enum (.value gives the string).
      GenieMessage.attachments is List[GenieAttachment] with
        .text (TextAttachment, .content) and .query (QueryAttachment, .query).

  w.genie.execute_message_query(space_id, conversation_id, message_id)
      -> GenieGetMessageQueryResultResponse
      POST .../messages/{message_id}/execute-query
      Called when status is EXECUTING_QUERY to trigger SQL execution.
      Per SDK docs, message status stays at EXECUTING_QUERY until this
      (or get_message_query_result) is called. Returns result immediately.

  w.genie.get_message_query_result(space_id, conversation_id, message_id)
      -> GenieGetMessageQueryResultResponse
      GET .../messages/{message_id}/query-result
      Called when status is COMPLETED to retrieve cached rows.

  GenieGetMessageQueryResultResponse.statement_response is StatementResponse
  (databricks.sdk.service.sql) with:
    .manifest.schema.columns  List[ColumnInfo] -- each .name is the col name
    .result.data_array         List[List[str]]  -- rows as string arrays

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

_ROW_CAP = 100  # maximum rows returned in poll() to keep response compact


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

    Raises:
        Any SDK exception (caller should catch and convert to HTTP 502).

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
    query attachment is present:
      - status EXECUTING_QUERY: calls execute_message_query() (POST) to
        trigger SQL execution and fetch rows. Per SDK docs, the message
        status will not advance past EXECUTING_QUERY until this is called.
      - status COMPLETED: calls get_message_query_result() (GET) to
        retrieve the cached rows.

    Rows are extracted from statement_response.manifest.schema.columns
    and statement_response.result.data_array, capped to _ROW_CAP rows.
    Row fetching is best-effort -- a failure only suppresses the rows key,
    it does not raise.

    DEPLOY-TIME NOTE: The EXECUTING_QUERY vs COMPLETED branching is
    source-verified but not end-to-end validated against a live space.
    If execute_message_query() does not return data_array synchronously
    (i.e., if execution is async), the frontend will see rows: absent on
    the EXECUTING_QUERY poll and should re-poll until COMPLETED, at which
    point get_message_query_result() returns the cached result.

    Raises:
        Any SDK exception from get_message() (caller converts to HTTP 502).

    Returns:
        {status: str, text: str | None}
        plus optional keys: {sql: str}, {rows: {columns: [...], data: [...]}}
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

    # Fetch query result rows when SQL is present and we are in a state where
    # the SDK can return data.
    if sql is not None and status in ("EXECUTING_QUERY", "COMPLETED"):
        try:
            if status == "EXECUTING_QUERY":
                # POST triggers execution; per SDK docs, status stays at
                # EXECUTING_QUERY until this endpoint is called.
                qr = w.genie.execute_message_query(
                    space_id=space_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                )
            else:
                # GET retrieves cached rows for a completed message.
                qr = w.genie.get_message_query_result(
                    space_id=space_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                )
            stmt = qr.statement_response
            if stmt and stmt.manifest and stmt.manifest.schema:
                columns = [
                    c.name
                    for c in (stmt.manifest.schema.columns or [])
                    if c.name
                ]
                raw_data = stmt.result.data_array if (stmt.result and stmt.result.data_array) else []
                result["rows"] = {"columns": columns, "data": list(raw_data[:_ROW_CAP])}
        except Exception:
            logger.debug(
                "Could not fetch Genie query result for message %s",
                message_id,
                exc_info=True,
            )

    return result
