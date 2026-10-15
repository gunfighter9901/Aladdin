import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { CitationText } from "../../components/CitationText";
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

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, liveText, liveTrace, busy]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    onSend(text);
  }

  return (
    <section className="panel" aria-label="Tutor">
      <div className="panel-head">
        <div>
          <h2>Tutor</h2>
          <p>Answers cite the checked sources. If the material does not contain it, the tutor says so.</p>
        </div>
        <div className="row">
          <button className="btn-ghost" type="button" onClick={onNewSession}>
            New
          </button>
          <button className="btn-danger" type="button" onClick={onDeleteSession} disabled={!sessionId}>
            Delete
          </button>
        </div>
      </div>
      <div className="scroll" style={{ paddingBottom: 8 }}>
        <label className="field">
          Session
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
        </label>
      </div>
      <div className="scroll chat-scroll">
        {messages.length === 0 && !busy ? (
          <div className="empty">
            <strong>Ask about the sources you checked.</strong>
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
            {message.trace.length > 0 ? <TraceList steps={message.trace} /> : null}
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
            {liveTrace.length > 0 ? <TraceList steps={liveTrace} /> : null}
          </article>
        ) : null}
        <div ref={endRef} />
      </div>
      <form className="composer" onSubmit={submit}>
        <label className="field">
          Question
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Ask a question, or ask for a podcast, quiz, or diagram."
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
          />
        </label>
        <button className="btn" type="submit" disabled={busy || !draft.trim() || !sessionId}>
          {busy ? "Thinking" : "Ask"}
        </button>
      </form>
    </section>
  );
}

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
