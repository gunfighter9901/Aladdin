import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createSession,
  deleteSession,
  deleteSource,
  generateInfographic,
  generatePodcast,
  generateQuiz,
  getCollection,
  getHealth,
  getSession,
  listSessions,
  listSources,
  streamChat,
  updateSource,
  uploadSource,
} from "./api";
import { ChatPanel } from "./features/chat/ChatPanel";
import { SourcePanel } from "./features/sources/SourcePanel";
import { StudioPanel } from "./features/studio/StudioPanel";
import type {
  Artifacts,
  ChatMessage,
  CollectionInfo,
  DiagramType,
  Health,
  Source,
  StudySession,
  TraceStep,
} from "./types";
import { artifactsFromSession, messageFrom } from "./utils/messages";

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [collection, setCollection] = useState<CollectionInfo | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [sessions, setSessions] = useState<StudySession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [studio, setStudio] = useState<Artifacts>({});
  const [banner, setBanner] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [status, setStatus] = useState("");
  const [liveTrace, setLiveTrace] = useState<TraceStep[]>([]);
  const [liveText, setLiveText] = useState("");
  const [studioBusy, setStudioBusy] = useState<"podcast" | "quiz" | "infographic" | null>(null);
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const [sourcesCollapsed, setSourcesCollapsed] = useState(false);
  const [confirm, setConfirm] = useState<{ title: string; detail: string; action: () => void } | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const openRequestRef = useRef(0);
  const chatAbortRef = useRef<AbortController | null>(null);

  const readySources = useMemo(
    () => sources.filter((source) => source.active && source.status === "ready"),
    [sources],
  );
  const readyCount = readySources.length;
  const readyIds = useMemo(() => readySources.map((source) => source.id), [readySources]);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    if (!highlightedId) return;
    const timer = window.setTimeout(() => setHighlightedId(null), 6000);
    return () => window.clearTimeout(timer);
  }, [highlightedId]);

  const refreshSources = useCallback(async () => {
    const [sourcesResult, collectionResult] = await Promise.allSettled([listSources(), getCollection()]);
    if (sourcesResult.status === "fulfilled") setSources(sourcesResult.value);
    if (collectionResult.status === "fulfilled") setCollection(collectionResult.value);
    if (sourcesResult.status === "rejected" && collectionResult.status === "rejected") {
      throw sourcesResult.reason;
    }
  }, []);

  const openSession = useCallback(async (id: string) => {
    chatAbortRef.current?.abort();
    chatAbortRef.current = null;
    setAsking(false);
    setLiveText("");
    setLiveTrace([]);
    const request = ++openRequestRef.current;
    const detail = await getSession(id);
    if (request !== openRequestRef.current) return;
    setSessionId(detail.session.id);
    setMessages(detail.messages);
    setStudio(artifactsFromSession(detail));
    setSessions((current) => {
      const without = current.filter((item) => item.id !== detail.session.id);
      return [detail.session, ...without];
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function boot() {
      try {
        const [healthResult, sessionsResult] = await Promise.allSettled([getHealth(), listSessions()]);
        if (cancelled) return;
        if (healthResult.status === "fulfilled") setHealth(healthResult.value);
        const nextSessions = sessionsResult.status === "fulfilled" ? sessionsResult.value : [];
        setSessions(nextSessions);
        try {
          await refreshSources();
        } catch {
          // Sources panel stays empty but chat/sessions still work.
        }
        if (cancelled) return;
        if (nextSessions[0]) {
          await openSession(nextSessions[0].id);
        } else {
          const created = await createSession();
          if (cancelled) return;
          setSessions([created]);
          setSessionId(created.id);
        }
        if (sessionsResult.status === "rejected") {
          setBanner(messageFrom(sessionsResult.reason, "The local API is not running."));
        } else if (healthResult.status === "rejected") {
          setBanner("Could not reach the health endpoint, but sessions loaded.");
        }
      } catch (error) {
        if (!cancelled) setBanner(messageFrom(error, "The local API is not running."));
      }
    }
    void boot();
    return () => {
      cancelled = true;
    };
  }, [openSession, refreshSources]);

  async function onUpload(form: FormData) {
    setUploading(true);
    setBanner(null);
    try {
      await uploadSource(form);
      await refreshSources();
    } catch (error) {
      setBanner(messageFrom(error, "That source could not be added."));
      await refreshSources().catch(() => undefined);
    } finally {
      setUploading(false);
    }
  }

  async function onToggle(source: Source, active: boolean) {
    setSources((current) => current.map((item) => (item.id === source.id ? { ...item, active } : item)));
    try {
      const updated = await updateSource(source.id, { active });
      setSources((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (error) {
      setBanner(messageFrom(error, "Could not update that source."));
      await refreshSources().catch(() => undefined);
    }
  }

  async function onDelete(source: Source) {
    setConfirm({
      title: `Delete ${source.title}?`,
      detail: "Its passages will leave the collection.",
      action: () => {
        setConfirm(null);
        void (async () => {
          try {
            await deleteSource(source.id);
            await refreshSources();
          } catch (error) {
            setBanner(messageFrom(error, "Could not delete that source."));
          }
        })();
      },
    });
  }

  async function onNewSession() {
    setBanner(null);
    try {
      const created = await createSession();
      setSessions((current) => [created, ...current]);
      setSessionId(created.id);
      setMessages([]);
      setStudio({});
    } catch (error) {
      setBanner(messageFrom(error, "Could not start a session."));
    }
  }

  async function onDeleteSession() {
    if (!sessionId) return;
    setConfirm({
      title: "Delete this study session?",
      detail: "Messages and linked studio artifacts will be removed.",
      action: () => {
        setConfirm(null);
        void (async () => {
          try {
            await deleteSession(sessionId);
            const remaining = sessions.filter((item) => item.id !== sessionId);
            setSessions(remaining);
            const next = remaining[0];
            if (next) await openSession(next.id);
            else await onNewSession();
          } catch (error) {
            setBanner(messageFrom(error, "Could not delete that session."));
          }
        })();
      },
    });
  }

  async function onSend(text: string) {
    if (!sessionId) return;
    // Cancel any in-flight stream so rapid sends do not interleave tokens.
    chatAbortRef.current?.abort();
    const controller = new AbortController();
    chatAbortRef.current = controller;
    const sendSessionId = sessionId;
    const activeSourceIds = [...readyIds];
    setBanner(null);
    setAsking(true);
    setStatus("The tutor is reading the selected sources.");
    setLiveText("");
    setLiveTrace([]);
    const optimisticId = `local-${Date.now()}`;
    const optimistic: ChatMessage = {
      id: optimisticId,
      role: "user",
      content: text,
      citations: [],
      trace: [],
      artifacts: {},
      createdAt: new Date().toISOString(),
    };
    setMessages((current) => [...current, optimistic]);
    let completed = false;
    let sawErrorEvent = false;
    try {
      await streamChat({ sessionId: sendSessionId, message: text, activeSourceIds }, (event) => {
        // The user may have switched sessions mid-stream; never append a
        // stale turn into the newly opened session.
        if (sessionIdRef.current !== sendSessionId && event.event === "done") {
          void listSessions().then(setSessions).catch(() => undefined);
          return;
        }
        if (sessionIdRef.current !== sendSessionId) return;
        if (event.event === "status") setStatus(event.data.text);
        if (event.event === "trace") setLiveTrace((current) => [...current, event.data]);
        if (event.event === "token") setLiveText((current) => current + event.data.text);
        if (event.event === "error") {
          sawErrorEvent = true;
          setBanner(event.data.message);
        }
        if (event.event === "done") {
          completed = true;
          setAsking(false);
          const saved: ChatMessage = {
            id: event.data.messageId,
            role: "assistant",
            content: event.data.content,
            citations: event.data.citations,
            trace: event.data.trace,
            artifacts: event.data.artifacts,
            createdAt: new Date().toISOString(),
          };
          setMessages((current) => [...current, saved]);
          setStudio((current) => ({ ...current, ...event.data.artifacts }));
          setLiveText("");
          setLiveTrace([]);
          void listSessions().then(setSessions).catch(() => undefined);
        }
      }, controller.signal);
    } catch (error) {
      // AbortError is an intentional cancel, not a failure banner.
      if (error instanceof DOMException && error.name === "AbortError") return;
      // Remove the optimistic user bubble so a failed send does not linger
      // as a phantom message; the error banner explains the failure.
      if (!completed) {
        setMessages((current) => current.filter((item) => item.id !== optimisticId));
      }
      // Stream-level error events already set the banner; only fall back here
      // for transport failures. Never show the raw ApiError object.
      if (!sawErrorEvent) {
        setBanner(messageFrom(error, "The tutor could not answer."));
      }
    } finally {
      if (sessionIdRef.current === sendSessionId) {
        setAsking(false);
      }
      if (chatAbortRef.current === controller) {
        chatAbortRef.current = null;
      }
    }
  }

  async function onGenerate(
    kind: "podcast" | "quiz" | "infographic",
    focus: string,
    diagramType: DiagramType,
    count: number,
  ) {
    const sourceIds = [...readyIds];
    if (!sourceIds.length) {
      setBanner("Select at least one source. An empty selection retrieves nothing.");
      return;
    }
    setStudioBusy(kind);
    setBanner(null);
    try {
      if (kind === "podcast") {
        const podcast = await generatePodcast({ sourceIds, sessionId: sessionId ?? undefined, focus });
        setStudio((current) => ({ ...current, podcast }));
      } else if (kind === "quiz") {
        const quiz = await generateQuiz({ sourceIds, sessionId: sessionId ?? undefined, focus, count });
        setStudio((current) => ({ ...current, quiz }));
      } else {
        const infographic = await generateInfographic({
          sourceIds,
          sessionId: sessionId ?? undefined,
          focus,
          diagramType,
        });
        setStudio((current) => ({ ...current, infographic }));
      }
    } catch (error) {
      setBanner(messageFrom(error, "The studio could not generate that."));
    } finally {
      setStudioBusy(null);
    }
  }

  return (
    <div className="app">
      {confirm ? (
        <div className="confirm-backdrop" role="presentation" onClick={() => setConfirm(null)}>
          <div
            className="confirm"
            role="alertdialog"
            aria-modal="true"
            aria-label={confirm.title}
            onClick={(event) => event.stopPropagation()}
          >
            <strong>{confirm.title}</strong>
            <p>{confirm.detail}</p>
            <div className="row">
              <button className="btn-ghost" type="button" onClick={() => setConfirm(null)}>
                Cancel
              </button>
              <button className="btn-danger" type="button" onClick={confirm.action}>
                Delete
              </button>
            </div>
          </div>
        </div>
      ) : null}
      <header className="topbar">
        <div className="brand">
          <div className="mark" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <path d="M4 15.5V5.2c2.2-1.4 4.4-.6 6 .6 1.6-1.2 3.8-2 6-.6v10.3c-2.2-1.2-4.4-.4-6 .7-1.6-1.1-3.8-1.9-6-.7Z" stroke="currentColor" strokeWidth="1.4" />
              <path d="M10 6.2v10.2" stroke="currentColor" strokeWidth="1.4" />
            </svg>
          </div>
          <div>
            <h1>Aladdin</h1>
            <p>The smart AI tutor that never leaves your machine.</p>
          </div>
        </div>
        <div className="pills">
          <span className="pill">
            <strong>{readyCount}</strong> sources in scope
          </span>
          <span className={`pill${health?.geminiConfigured ? "" : " warn"}`}>
            {health?.geminiConfigured ? "Gemini ready" : "Gemini key missing"}
          </span>
          <span className="pill">{health?.opikConfigured ? "Opik tracing on" : "Opik not configured"}</span>
        </div>
        {banner ? (
          <div className="banner" role="status">
            {banner}
          </div>
        ) : null}
      </header>
      <main className={`workspace${sourcesCollapsed ? " sources-collapsed" : ""}`}>
        <SourcePanel
          sources={sources}
          collection={collection}
          busy={uploading}
          highlightedId={highlightedId}
          collapsed={sourcesCollapsed}
          onToggleCollapse={() => setSourcesCollapsed((value) => !value)}
          onUpload={onUpload}
          onToggle={onToggle}
          onDelete={onDelete}
          onError={setBanner}
        />
        <ChatPanel
          sessions={sessions}
          sessionId={sessionId}
          messages={messages}
          busy={asking}
          status={status}
          liveTrace={liveTrace}
          liveText={liveText}
          onNewSession={() => void onNewSession()}
          onSelectSession={(id) => void openSession(id)}
          onDeleteSession={() => void onDeleteSession()}
          onSend={(text) => void onSend(text)}
          onOpenSource={setHighlightedId}
        />
        <StudioPanel
          artifacts={studio}
          busy={studioBusy}
          canGenerate={readyCount > 0}
          onGenerate={(kind, focus, diagramType, count) => void onGenerate(kind, focus, diagramType, count)}
        />
      </main>
    </div>
  );
}
