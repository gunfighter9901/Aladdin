import { memo, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { CitationText } from "../../components/CitationText";
import { MAX_MESSAGE_LENGTH } from "../../config";
import type { ChatMessage, StudySession, TraceStep } from "../../types";

type Props = {
  sessions: StudySession[];
  sessionId: string | null;
  messages: ChatMessage[];
  busy: boolean;
  status: string;
  liveTrace: TraceStep[];
  liveText: string;
  onNewSession: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: () => void;
  onSend: (text: string) => void;
  onOpenSource: (sourceId: string) => void;
};

export function ChatPanel({
  sessions,
  sessionId,
  messages,
  busy,
  status,
  liveTrace,
  liveText,
  onNewSession,
  onSelectSession,
  onDeleteSession,
  onSend,
  onOpenSource,
}: Props) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !stickRef.current) return;
    const frame = requestAnimationFrame(() => {
      endRef.current?.scrollIntoView({ block: "end" });
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, liveText, liveTrace, busy]);

  function onScroll() {
    const node = scrollRef.current;
    if (!node) return;
    stickRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 160;
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    onSend(text);
  }

  return (
    <section className="panel" aria-label="Tutor">
      <div className="session-bar">
        <div className="session-select grow">
          <select
            value={sessionId ?? ""}
            onChange={(event) => onSelectSession(event.target.value)}
            aria-label="Study session"
          >
            {sessions.length === 0 ? <option value="">No sessions yet</option> : null}
            {sessions.map((session) => (
              <option key={session.id} value={session.id}>
                {session.title}
              </option>
            ))}
          </select>
          <span className="session-tag" aria-hidden="true">
            Session
          </span>
        </div>
        <div className="row session-actions">
          <button className="btn-ghost session-btn" type="button" onClick={onNewSession}>
            New
          </button>
          <button className="btn-danger session-btn" type="button" onClick={onDeleteSession} disabled={!sessionId}>
            Delete
          </button>
        </div>
      </div>
      <div className="scroll chat-scroll" ref={scrollRef} onScroll={onScroll}>
        {messages.length === 0 && !busy ? (
          <div className="empty chat-empty">
            <div className="chat-empty-title">Ask about the sources you checked.</div>
            <p>Try “Explain the main argument” or “Make a quiz on the second source.”</p>
          </div>
        ) : null}
        {messages.map((message) => (
          <article key={message.id} className={`bubble ${message.role === "user" ? "user" : "assistant"}`}>
            <span className="who">{message.role === "user" ? "You" : "Tutor"}</span>
            {message.role === "assistant" ? (
              <CitationText text={message.content} citations={message.citations} onOpenSource={onOpenSource} />
            ) : (
              message.content
            )}
            {message.trace.length > 0 ? <TraceToggle steps={message.trace} /> : null}
          </article>
        ))}
        {busy ? (
          <article className="bubble assistant">
            <span className="who">Tutor</span>
            {liveText ? (
              <CitationText text={liveText} citations={[]} />
            ) : (
              <p className="hint">{status || "Working through the sources…"}</p>
            )}
            {liveTrace.length > 0 ? <TraceToggle steps={liveTrace} /> : null}
          </article>
        ) : null}
        <div ref={endRef} />
      </div>
      <form className="composer" onSubmit={submit}>
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask a question, or ask for a podcast, quiz, or diagram."
          aria-label="Question"
          maxLength={MAX_MESSAGE_LENGTH}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <button className="btn" type="submit" disabled={busy || !draft.trim() || !sessionId}>
          {busy ? "Thinking" : "Ask"}
        </button>
      </form>
    </section>
  );
}

function TraceToggle({ steps }: { steps: TraceStep[] }) {
  return <TraceToggleInner steps={steps} />;
}

const TraceToggleInner = memo(function TraceToggleInner({ steps }: { steps: TraceStep[] }) {
  const toolNames = steps
    .filter((step) => step.type === "tool_call")
    .map((step) => step.name ?? "tool")
    .filter((name, index, all) => all.indexOf(name) === index);
  const label = toolNames.length > 0 ? `Tools: ${toolNames.join(", ")}` : `Trace (${steps.length})`;
  return (
    <details className="trace-toggle">
      <summary title="Show tool calls and retrieved chunks">{label}</summary>
      <TraceList steps={steps} />
    </details>
  );
});

function TraceList({ steps }: { steps: TraceStep[] }) {
  return (
    <div className="trace">
      {steps.map((step, index) => (
        <div className="trace-item" key={`${step.type}-${step.name ?? "step"}-${index}`}>
          <strong>{labelFor(step)}</strong>
          {step.text ? <div>{step.text}</div> : null}
          {step.type === "tool_result" && step.result ? (
            <div>{summarize(step.result)}</div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function labelFor(step: TraceStep) {
  if (step.type === "tool_call") return `Call ${step.name ?? "tool"}`;
  if (step.type === "tool_result") return `Result ${step.name ?? "tool"}`;
  return step.name || "Note";
}

function summarize(result: unknown) {
  if (!result || typeof result !== "object") return "";
  const record = result as { status?: string; message?: string; hitCount?: number; hits?: unknown[]; preview?: string };
  if (Array.isArray(record.hits)) {
    const names = record.hits
      .map((hit) => (hit && typeof hit === "object" && "chunkId" in hit ? String(hit.chunkId) : ""))
      .filter(Boolean)
      .slice(0, 4)
      .join(", ");
    return `${record.hits.length} passages${names ? `: ${names}` : ""}`;
  }
  return record.message || record.status || record.preview || "";
}
