"use client";

import type { Message } from "@/components/PlaygroundClient";

interface MessageEditorProps {
  message: Message;
  onChange: (next: Message) => void;
  onDelete: () => void;
  /** Undefined when this is the first message. */
  onMoveUp?: () => void;
  /** Undefined when this is the last message. */
  onMoveDown?: () => void;
  /** Disables delete when there's only one message left. */
  canDelete: boolean;
}

const ROLE_LABEL: Record<Message["role"], string> = {
  system: "SYSTEM",
  human: "HUMAN",
};

export function MessageEditor({
  message,
  onChange,
  onDelete,
  onMoveUp,
  onMoveDown,
  canDelete,
}: MessageEditorProps) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--r-4)",
        overflow: "hidden",
        background: "var(--surface-sidebar)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 12px",
          background: "var(--surface-2)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            cursor: "pointer",
          }}
        >
          <span
            style={{
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: "0.08em",
              color: "var(--text-4)",
              minWidth: 56,
            }}
          >
            {ROLE_LABEL[message.role]}
          </span>
          <select
            aria-label="message role"
            value={message.role}
            onChange={(e) =>
              onChange({
                ...message,
                role: e.target.value as Message["role"],
              })
            }
            style={{
              fontSize: 12,
              width: "auto",
              color: "var(--text-2)",
              padding: "4px 14px",
              cursor: "pointer",
            }}
          >
            <option value="system">System</option>
            <option value="human">Human</option>
          </select>
        </label>
        <div style={{ display: "flex", gap: 2 }}>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onMoveUp}
            disabled={!onMoveUp}
            aria-label="move message up"
            style={{ minWidth: 28, padding: "0 8px" }}
          >
            &uarr;
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onMoveDown}
            disabled={!onMoveDown}
            aria-label="move message down"
            style={{ minWidth: 28, padding: "0 8px" }}
          >
            &darr;
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onDelete}
            disabled={!canDelete}
            aria-label="delete message"
            style={{ minWidth: 28, padding: "0 8px" }}
          >
            &times;
          </button>
        </div>
      </div>
      <textarea
        value={message.content}
        onChange={(e) => onChange({ ...message, content: e.target.value })}
        placeholder={
          message.role === "system"
            ? "You are a helpful assistant..."
            : "Write the user turn. Use {{ var }} for variables."
        }
        rows={Math.max(2, Math.min(12, message.content.split("\n").length + 1))}
        style={{
          width: "100%",
          padding: "12px 14px",
          border: 0,
          borderRadius: 0,
          resize: "vertical",
          fontSize: 13,
          fontFamily: "inherit",
          background: "var(--surface-sidebar)",
          color: "var(--text)",
          boxSizing: "border-box",
          outline: "none",
        }}
      />
    </div>
  );
}
