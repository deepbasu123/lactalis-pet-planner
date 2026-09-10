/**
 * GeniePanel.tsx
 *
 * Collapsible right-side dock for asking questions of the Genie space.
 * Intentionally kept secondary -- the grids are the hero. The panel slides
 * in over the right edge at 200 ms and stays out of the way when closed.
 *
 * Polling contract (confirmed from backend/genie.py and backend/main.py):
 *   POST /api/genie/ask  body: {question, conversation_id?}
 *                        -> {conversation_id, message_id}
 *                        || HTTP 503 (space not configured)
 *   GET  /api/genie/poll ?conversation_id=&message_id=
 *                        -> {status, text, sql?, rows?}
 *                           rows = {columns: string[], data: unknown[][]}
 *                        || HTTP 503
 *
 * 503 renders a friendly "not configured" state, never an error message.
 * All other errors surface a short inline message in the message list.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import type { CSSProperties } from 'react';
import { genieAsk, geniePoll } from '../api';
import type { GeniePollResponse } from '../api';
import {
  shouldContinuePolling,
  capRows,
  isGenieNotConfigured,
} from './geniePanelHelpers';
import './GeniePanel.css';

// ── Types ─────────────────────────────────────────────────────────────────────

interface GenieMessage {
  role: 'user' | 'assistant';
  text: string | null;            // null while loading
  sql?: string;
  rows?: { columns: string[]; data: unknown[][] };
  loading?: boolean;              // true while waiting for poll to complete
  error?: boolean;                // true when the request failed
}

interface GeniePanelProps {
  isOpen: boolean;
  onClose: () => void;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const SAMPLE_QUESTIONS = [
  'Which SKUs run short before week 45?',
  'Total production for the OAK 500ml SKUs?',
  'Which weeks breach capacity?',
] as const;

const POLL_INTERVAL_MS = 1200;
const MAX_POLL_ATTEMPTS = 30;
const MAX_TABLE_ROWS = 20;

// ── Helper ────────────────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ── Typing indicator ──────────────────────────────────────────────────────────

function TypingDots() {
  return (
    <span
      style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 0' }}
      aria-label="Genie is thinking"
    >
      <span className="genie-dot" />
      <span className="genie-dot" />
      <span className="genie-dot" />
    </span>
  );
}

// ── SQL collapsible ───────────────────────────────────────────────────────────

function SqlBlock({ sql }: { sql: string }) {
  return (
    <details className="genie-sql-details">
      <summary className="genie-sql-summary">View SQL</summary>
      <pre className="genie-sql-pre">{sql}</pre>
    </details>
  );
}

// ── Result table ──────────────────────────────────────────────────────────────

function RowsTable({ columns, data }: { columns: string[]; data: unknown[][] }) {
  if (columns.length === 0) return null;
  const display = capRows(data, MAX_TABLE_ROWS);
  return (
    <div style={{ marginTop: 8 }}>
      <div className="genie-table-wrap">
        <table className="genie-table" aria-label="Query results">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {display.map((row, ri) => (
              <tr key={ri}>
                {columns.map((_, ci) => (
                  <td key={ci}>{String((row as unknown[])[ci] ?? '')}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.length > MAX_TABLE_ROWS && (
        <p style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 4 }}>
          Showing {MAX_TABLE_ROWS} of {data.length} rows
        </p>
      )}
    </div>
  );
}

// ── Message bubble ────────────────────────────────────────────────────────────

function MessageBubble({ msg }: { msg: GenieMessage }) {
  const isUser = msg.role === 'user';

  const bubbleStyle: CSSProperties = {
    maxWidth: '86%',
    padding: '8px 12px',
    fontSize: 13,
    lineHeight: 1.55,
    borderRadius: 'var(--radius-sm)',
    ...(isUser
      ? {
          background: 'var(--lac-blue)',
          color: '#fff',
          alignSelf: 'flex-end',
          borderBottomRightRadius: 0,
        }
      : {
          background: 'var(--surface-1)',
          color: 'var(--text-primary)',
          alignSelf: 'flex-start',
          border: '1px solid var(--border-light)',
          borderBottomLeftRadius: 0,
        }),
    ...(msg.error
      ? {
          background: '#fff5f5',
          border: '1px solid #fed7d7',
          color: '#c53030',
        }
      : {}),
  };

  return (
    <div style={bubbleStyle}>
      {msg.loading ? (
        <TypingDots />
      ) : (
        <>
          {msg.text && <span>{msg.text}</span>}
          {msg.sql && <SqlBlock sql={msg.sql} />}
          {msg.rows && msg.rows.columns.length > 0 && (
            <RowsTable columns={msg.rows.columns} data={msg.rows.data} />
          )}
        </>
      )}
    </div>
  );
}

// ── Disabled state ────────────────────────────────────────────────────────────

function DisabledNotice() {
  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '0 24px',
        textAlign: 'center',
        gap: 12,
      }}
    >
      <span
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 44,
          height: 44,
          border: '2px solid var(--border)',
          color: 'var(--text-muted)',
          fontSize: 20,
        }}
        aria-hidden="true"
      >
        &#9649;
      </span>
      <p
        style={{
          fontSize: 13,
          color: 'var(--text-muted)',
          lineHeight: 1.6,
          margin: 0,
        }}
      >
        Genie is not configured for this workspace yet.
      </p>
      <p
        style={{
          fontSize: 11,
          color: 'var(--text-muted)',
          lineHeight: 1.5,
          margin: 0,
        }}
      >
        Set <code style={{ fontFamily: 'var(--font-mono)', background: 'var(--surface-2)', padding: '1px 4px' }}>PET_GENIE_SPACE_ID</code> in the app environment to enable chat.
      </p>
    </div>
  );
}

// ── Sample question chips ─────────────────────────────────────────────────────

function SampleChips({ onSelect, disabled }: { onSelect: (q: string) => void; disabled: boolean }) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        padding: '0 16px 12px',
      }}
    >
      <p style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600, marginBottom: 2 }}>
        Try asking
      </p>
      {SAMPLE_QUESTIONS.map((q) => (
        <button
          key={q}
          onClick={() => onSelect(q)}
          disabled={disabled}
          style={{
            textAlign: 'left',
            padding: '7px 10px',
            fontSize: 12,
            lineHeight: 1.45,
            color: disabled ? 'var(--text-muted)' : 'var(--lac-blue)',
            background: 'var(--bg)',
            border: `1px solid ${disabled ? 'var(--border-light)' : 'var(--lac-sky)'}`,
            borderLeft: `3px solid ${disabled ? 'var(--border)' : 'var(--lac-blue)'}`,
            cursor: disabled ? 'not-allowed' : 'pointer',
            transition: 'background-color var(--ease-fast), border-color var(--ease-fast)',
            borderRadius: 'var(--radius-sm)',
          }}
          onMouseEnter={(e) => {
            if (!disabled) {
              (e.currentTarget as HTMLButtonElement).style.background = 'var(--surface-1)';
            }
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg)';
          }}
        >
          {q}
        </button>
      ))}
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function GeniePanel({ isOpen, onClose }: GeniePanelProps) {
  const [messages, setMessages] = useState<GenieMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isAsking, setIsAsking] = useState(false);
  const [genieDisabled, setGenieDisabled] = useState(false);

  // Persists the conversation across follow-up questions in the same session.
  const conversationIdRef = useRef<string | null>(null);

  // Set to true on unmount so in-flight polling loops abort safely.
  const abortRef = useRef(false);

  // Scroll anchor at the bottom of the message list.
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Input ref for focus management.
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    return () => {
      abortRef.current = true;
    };
  }, []);

  // Auto-scroll to bottom when messages change.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus input when panel opens.
  useEffect(() => {
    if (isOpen && !genieDisabled) {
      setTimeout(() => inputRef.current?.focus(), 220); // after transition
    }
  }, [isOpen, genieDisabled]);

  // ── Poll loop ──────────────────────────────────────────────────────────────

  const pollUntilDone = useCallback(
    async (conversationId: string, messageId: string) => {
      for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
        await sleep(POLL_INTERVAL_MS);
        if (abortRef.current) return;

        let pollResult: GeniePollResponse;
        try {
          pollResult = await geniePoll(conversationId, messageId);
        } catch (err) {
          if (abortRef.current) return;
          if (isGenieNotConfigured(err)) {
            setGenieDisabled(true);
            setMessages((prev) => prev.filter((m) => !m.loading));
          } else {
            setMessages((prev) =>
              prev.map((m) =>
                m.loading
                  ? {
                      role: 'assistant',
                      text: 'Could not get an answer right now. Please try again.',
                      loading: false,
                      error: true,
                    }
                  : m,
              ),
            );
          }
          return;
        }

        if (!shouldContinuePolling(pollResult.status ?? undefined)) {
          if (abortRef.current) return;
          // Terminal state reached -- replace the loading placeholder.
          setMessages((prev) =>
            prev.map((m) =>
              m.loading
                ? {
                    role: 'assistant' as const,
                    text: pollResult.text ?? 'No answer returned.',
                    sql: pollResult.sql,
                    rows: pollResult.rows,
                    loading: false,
                  }
                : m,
            ),
          );
          return;
        }
      }

      if (abortRef.current) return;
      // Max attempts exceeded.
      setMessages((prev) =>
        prev.map((m) =>
          m.loading
            ? {
                role: 'assistant',
                text: 'Genie took too long to respond. Please try again.',
                loading: false,
                error: true,
              }
            : m,
        ),
      );
    },
    [],
  );

  // ── Ask handler ────────────────────────────────────────────────────────────

  const handleAsk = useCallback(
    async (question: string) => {
      if (isAsking || genieDisabled || !question.trim()) return;

      const trimmed = question.trim();
      setInputValue('');

      // Append user message and loading placeholder together.
      setMessages((prev) => [
        ...prev,
        { role: 'user', text: trimmed },
        { role: 'assistant', text: null, loading: true },
      ]);

      setIsAsking(true);

      try {
        let askResult: { conversation_id: string; message_id: string };
        try {
          askResult = await genieAsk({
            question: trimmed,
            conversation_id: conversationIdRef.current ?? undefined,
          });
        } catch (err) {
          if (isGenieNotConfigured(err)) {
            setGenieDisabled(true);
            setMessages((prev) => prev.filter((m) => !m.loading));
            return;
          }
          setMessages((prev) =>
            prev.map((m) =>
              m.loading
                ? {
                    role: 'assistant',
                    text: 'Could not reach Genie right now. Please try again.',
                    loading: false,
                    error: true,
                  }
                : m,
            ),
          );
          return;
        }

        // Remember the conversation so follow-ups are threaded.
        conversationIdRef.current = askResult.conversation_id;

        await pollUntilDone(askResult.conversation_id, askResult.message_id);
      } finally {
        if (!abortRef.current) {
          setIsAsking(false);
        }
      }
    },
    [isAsking, genieDisabled, pollUntilDone],
  );

  // ── Submit handlers ────────────────────────────────────────────────────────

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void handleAsk(inputValue);
  };

  const handleChipSelect = (q: string) => {
    void handleAsk(q);
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  const showSampleChips = messages.length === 0;

  return (
    <aside
      className="genie-panel"
      aria-label="Ask Genie"
      aria-hidden={!isOpen}
      style={{
        position: 'fixed',
        top: 'var(--header-height)',
        right: 0,
        width: 340,
        height: 'calc(100vh - var(--header-height))',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg)',
        borderLeft: '1px solid var(--border)',
        boxShadow: isOpen ? 'var(--shadow-lg)' : 'none',
        zIndex: 200,
        transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
        transition: 'transform 200ms ease',
        overflow: 'hidden',
      }}
    >
      {/* ── Panel header ───────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 16px',
          height: 48,
          background: 'var(--lac-blue)',
          flexShrink: 0,
          gap: 8,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span
            style={{
              fontSize: 16,
              color: 'var(--lac-sky)',
              lineHeight: 1,
              fontWeight: 700,
            }}
            aria-hidden="true"
          >
            &#9670;
          </span>
          <span
            style={{
              fontSize: 13,
              fontWeight: 700,
              color: '#fff',
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
            }}
          >
            Ask Genie
          </span>
        </div>
        <button
          onClick={onClose}
          aria-label="Close Genie panel"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 28,
            height: 28,
            background: 'rgba(255,255,255,0.12)',
            border: 'none',
            color: '#fff',
            fontSize: 16,
            cursor: 'pointer',
            flexShrink: 0,
            borderRadius: 'var(--radius-sm)',
            lineHeight: 1,
            transition: 'background-color var(--ease-fast)',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.22)';
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = 'rgba(255,255,255,0.12)';
          }}
        >
          &times;
        </button>
      </div>

      {/* ── Disabled state ─────────────────────────────────────────── */}
      {genieDisabled ? (
        <DisabledNotice />
      ) : (
        <>
          {/* ── Message list ─────────────────────────────────────── */}
          <div
            className="genie-messages"
            style={{
              flex: 1,
              overflowY: 'auto',
              display: 'flex',
              flexDirection: 'column',
              gap: 10,
              padding: '14px 12px 4px',
            }}
            role="log"
            aria-live="polite"
            aria-label="Conversation"
          >
            {messages.length === 0 && (
              <p
                style={{
                  fontSize: 12,
                  color: 'var(--text-muted)',
                  lineHeight: 1.6,
                  textAlign: 'center',
                  padding: '8px 8px 4px',
                }}
              >
                Ask a question about the PET plan data below, or pick one of the suggestions.
              </p>
            )}
            {messages.map((msg, i) => (
              <MessageBubble key={i} msg={msg} />
            ))}
            <div ref={messagesEndRef} />
          </div>

          {/* ── Sample chips (shown only before first message) ────── */}
          {showSampleChips && (
            <SampleChips onSelect={handleChipSelect} disabled={isAsking} />
          )}

          {/* ── Input form ────────────────────────────────────────── */}
          <form
            onSubmit={handleSubmit}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '10px 12px',
              borderTop: '1px solid var(--border-light)',
              background: 'var(--bg)',
              flexShrink: 0,
            }}
          >
            <input
              ref={inputRef}
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Ask about the plan..."
              disabled={isAsking}
              aria-label="Question for Genie"
              style={{
                flex: 1,
                height: 34,
                padding: '0 10px',
                fontSize: 13,
                color: 'var(--text-primary)',
                background: isAsking ? 'var(--surface-1)' : 'var(--bg)',
                border: '1px solid var(--border)',
                outline: 'none',
                fontFamily: 'var(--font-sans)',
                borderRadius: 'var(--radius-sm)',
                transition: 'border-color var(--ease-fast), background-color var(--ease-fast)',
              }}
              onFocus={(e) => {
                (e.currentTarget as HTMLInputElement).style.borderColor = 'var(--lac-blue)';
              }}
              onBlur={(e) => {
                (e.currentTarget as HTMLInputElement).style.borderColor = 'var(--border)';
              }}
            />
            <button
              type="submit"
              disabled={isAsking || !inputValue.trim()}
              aria-label="Send"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 34,
                height: 34,
                background:
                  isAsking || !inputValue.trim()
                    ? 'var(--surface-2)'
                    : 'var(--lac-blue)',
                border: 'none',
                color:
                  isAsking || !inputValue.trim()
                    ? 'var(--text-muted)'
                    : '#fff',
                cursor:
                  isAsking || !inputValue.trim() ? 'not-allowed' : 'pointer',
                fontSize: 16,
                flexShrink: 0,
                borderRadius: 'var(--radius-sm)',
                transition:
                  'background-color var(--ease-base), color var(--ease-base)',
              }}
            >
              {/* Right-pointing triangle (send) */}
              &#9658;
            </button>
          </form>
        </>
      )}
    </aside>
  );
}
