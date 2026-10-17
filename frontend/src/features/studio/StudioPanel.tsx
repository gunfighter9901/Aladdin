import { useEffect, useState } from "react";
import { MAX_FOCUS_LENGTH } from "../../config";
import type { Artifacts, DiagramType, InfographicPayload, PodcastPayload, QuizPayload } from "../../types";

type Kind = "podcast" | "quiz" | "infographic";

type Props = {
  artifacts: Artifacts;
  busy: Kind | null;
  canGenerate: boolean;
  onGenerate: (kind: Kind, focus: string, diagramType: DiagramType, count: number) => void;
};

const TABS: Array<{ id: Kind; label: string }> = [
  { id: "podcast", label: "Podcast" },
  { id: "quiz", label: "Quiz" },
  { id: "infographic", label: "Diagram" },
];

export function StudioPanel({ artifacts, busy, canGenerate, onGenerate }: Props) {
  const [tab, setTab] = useState<Kind>("podcast");
  const [focus, setFocus] = useState("");
  const [diagramType, setDiagramType] = useState<DiagramType>("mindmap");
  const [count, setCount] = useState(5);

  function clampCount(value: number) {
    if (!Number.isFinite(value)) return 5;
    return Math.min(12, Math.max(1, Math.floor(value)));
  }

  return (
    <section className="panel" aria-label="Studio">
      <div className="panel-head">
        <div>
          <h2>Studio</h2>
          <p>Podcasts, Quiz, and diagrams from the checked sources only.</p>
        </div>
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="studio-body">
        <label className="field">
          Focus
          <input
            value={focus}
            maxLength={MAX_FOCUS_LENGTH}
            onChange={(event) => setFocus(event.target.value)}
            placeholder="Optional, such as the water cycle"
          />
        </label>
        {tab === "quiz" ? (
          <label className="field">
            Questions (1–12)
            <input
              type="number"
              min={1}
              max={12}
              step={1}
              inputMode="numeric"
              value={count}
              onChange={(event) => setCount(clampCount(Number(event.target.value)))}
              onBlur={() => setCount((value) => clampCount(value))}
            />
          </label>
        ) : null}
        {tab === "infographic" ? (
          <label className="field">
            Diagram
            <select value={diagramType} onChange={(event) => setDiagramType(event.target.value as DiagramType)}>
              <option value="mindmap">Mindmap</option>
              <option value="flowchart">Flowchart</option>
              <option value="timeline">Timeline</option>
              <option value="sequence">Sequence</option>
            </select>
          </label>
        ) : null}
        <button
          className="btn secondary"
          type="button"
          disabled={!canGenerate || busy !== null}
          onClick={() => onGenerate(tab, focus, diagramType, count)}
        >
          {busy === tab ? "Making it" : `Generate ${tab}`}
        </button>
        {!canGenerate ? <p className="hint">Check at least one ready source. An empty selection retrieves nothing.</p> : null}
        {tab === "podcast" ? <PodcastView podcast={artifacts.podcast} /> : null}
        {tab === "quiz" ? <QuizView quiz={artifacts.quiz} /> : null}
        {tab === "infographic" ? <DiagramView diagram={artifacts.infographic} /> : null}
      </div>
    </section>
  );
}

function PodcastView({ podcast }: { podcast?: PodcastPayload }) {
  const [objectUrl, setObjectUrl] = useState("");

  useEffect(() => {
    if (!podcast?.audioBase64) {
      setObjectUrl("");
      return;
    }
    // Guard the main thread: refuse absurd payloads instead of locking the tab in atob().
    if (podcast.audioBase64.length > 40_000_000) {
      setObjectUrl("");
      return;
    }
    let url = "";
    try {
      const binary = atob(podcast.audioBase64);
      const bytes = new Uint8Array(binary.length);
      const step = 8192;
      for (let offset = 0; offset < binary.length; offset += step) {
        const end = Math.min(offset + step, binary.length);
        for (let i = offset; i < end; i++) bytes[i] = binary.charCodeAt(i);
      }
      const blob = new Blob([bytes.buffer as ArrayBuffer], { type: podcast.mimeType || "audio/wav" });
      url = URL.createObjectURL(blob);
      setObjectUrl(url);
    } catch {
      setObjectUrl("");
    }
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [podcast?.audioBase64, podcast?.mimeType]);

  if (!podcast) {
    return (
      <div className="empty">
        <strong>No podcast yet.</strong>
        <p>Joe (Puck) hosts. Jane (Kore) explains. Audio is 24 kHz.</p>
      </div>
    );
  }
  const src = objectUrl;
  const downloadName = `${(podcast.title || "podcast").replace(/[^\w\- ]+/g, "").trim() || "podcast"}.wav`;
  return (
    <div className="stack">
      <strong>{podcast.title}</strong>
      {podcast.truncated ? (
        <p className="hint">Long scripts are cut at a turn boundary so speech synthesis stays reliable.</p>
      ) : null}
      {src ? <audio controls src={src} /> : <p className="hint">{podcast.audioError || "Script is ready. Audio was not returned."}</p>}
      {src ? (
        <a className="btn-ghost" href={src} download={downloadName} style={{ textAlign: "center" }}>
          Download audio
        </a>
      ) : null}
      <div>
        {podcast.turns.map((turn, index) => (
          <div className="script-turn" key={`${turn.speaker}-${index}`}>
            <div className={`speaker ${turn.speaker.toLowerCase()}`}>{turn.speaker}</div>
            <div>{turn.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function QuizView({ quiz }: { quiz?: QuizPayload }) {
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [card, setCard] = useState(0);
  const [flipped, setFlipped] = useState(false);

  // Reset whenever a genuinely new quiz arrives: same title but different
  // questions must not keep stale answers.
  const quizKey = quiz ? `${quiz.title}::${quiz.questions.map((q) => q.id).join(",")}::${quiz.questions.length}` : "";
  useEffect(() => {
    setPicked({});
    setRevealed({});
    setCard(0);
    setFlipped(false);
  }, [quizKey]);

  if (!quiz) {
    return (
      <div className="empty">
        <strong>No quiz yet.</strong>
        <p>Questions stay tied to the selected materials, with a rationale for each answer.</p>
      </div>
    );
  }
  const correct = quiz.questions.filter((question) => revealed[question.id] && picked[question.id] === question.correctAnswer).length;
  const current = quiz.flashcards[card];
  return (
    <div className="stack">
      <strong>{quiz.title}</strong>
      {typeof quiz.requestedCount === "number" && typeof quiz.returnedCount === "number" && quiz.returnedCount < quiz.requestedCount ? (
        <p className="hint">
          {quiz.returnedCount} of {quiz.requestedCount} requested questions were usable.
        </p>
      ) : null}
      {Object.keys(revealed).length > 0 ? (
        <div className="score">
          {correct} / {quiz.questions.length} correct so far
        </div>
      ) : null}
      {quiz.questions.map((question) => {
        const choice = picked[question.id];
        const open = revealed[question.id];
        return (
          <div className="question" key={question.id}>
            <div>{question.question}</div>
            <div className="options">
              {question.options.map((option) => {
                const className = [
                  "option",
                  choice === option ? "picked" : "",
                  open && option === question.correctAnswer ? "correct" : "",
                  open && choice === option && option !== question.correctAnswer ? "wrong" : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <button
                    key={option}
                    type="button"
                    className={className}
                    onClick={() => setPicked((currentPicks) => ({ ...currentPicks, [question.id]: option }))}
                  >
                    {option}
                  </button>
                );
              })}
            </div>
            <button
              className="btn-ghost"
              type="button"
              disabled={!choice}
              onClick={() => setRevealed((currentRevealed) => ({ ...currentRevealed, [question.id]: true }))}
            >
              Check
            </button>
            {open ? <div className="rationale">{question.rationale}</div> : null}
          </div>
        );
      })}
      {current ? (
        <button className="card-flip" type="button" onClick={() => setFlipped((value) => !value)}>
          <small>
            Flashcard {card + 1} / {quiz.flashcards.length} · click to flip
          </small>
          {flipped ? current.back : current.front}
        </button>
      ) : null}
      {quiz.flashcards.length > 1 ? (
        <div className="row">
          <button className="btn-ghost" type="button" onClick={() => { setCard((value) => Math.max(0, value - 1)); setFlipped(false); }}>
            Previous
          </button>
          <button
            className="btn-ghost"
            type="button"
            onClick={() => {
              setCard((value) => Math.min(quiz.flashcards.length - 1, value + 1));
              setFlipped(false);
            }}
          >
            Next
          </button>
        </div>
      ) : null}
      <QuizDownload quiz={quiz} />
    </div>
  );
}

function QuizDownload({ quiz }: { quiz: QuizPayload }) {
  const [downloadUrl, setDownloadUrl] = useState("");
  useEffect(() => {
    const body = quiz.questions
      .map(
        (question, index) =>
          `${index + 1}. ${question.question}\n${question.options.map((option) => `   - ${option}`).join("\n")}\n   Answer: ${question.correctAnswer}\n   Why: ${question.rationale}`,
      )
      .join("\n\n");
    const url = URL.createObjectURL(new Blob([`${quiz.title}\n\n${body}`], { type: "text/plain" }));
    setDownloadUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [quiz]);
  return (
    <a
      className="btn-ghost"
      style={{ textAlign: "center" }}
      href={downloadUrl}
      download={`${(quiz.title || "quiz").replace(/[^\w\- ]+/g, "").trim() || "quiz"}.txt`}
    >
      Download quiz
    </a>
  );
}

let mermaidInitialized = false;

async function renderMermaid(id: string, markup: string): Promise<string> {
  // Lazy-load the heaviest dependency only when a diagram actually renders.
  const { default: mermaid } = await import("mermaid");
  if (!mermaidInitialized) {
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "dark" });
    mermaidInitialized = true;
  }
  const result = await mermaid.render(id, markup);
  return result.svg;
}

function DiagramView({ diagram }: { diagram?: InfographicPayload }) {
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [showCode, setShowCode] = useState(false);

  useEffect(() => {
    if (!diagram?.mermaid) {
      setSvg("");
      setError("");
      return;
    }
    // Cap markup so a pathological response cannot hang the renderer.
    if (diagram.mermaid.length > 20000) {
      setSvg("");
      setError("This diagram is too large to draw. The markup is below.");
      return;
    }
    let cancelled = false;
    const id = `diagram-${Math.random().toString(36).slice(2)}`;
    renderMermaid(id, diagram.mermaid)
      .then((rendered) => {
        if (!cancelled) {
          setSvg(rendered);
          setError("");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSvg("");
          setError("This diagram could not be drawn. The markup is below.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [diagram?.mermaid]);

  if (!diagram) {
    return (
      <div className="empty">
        <strong>No diagram yet.</strong>
        <p>Mindmaps, flowcharts, timelines, and sequence diagrams are drawn with Mermaid.</p>
      </div>
    );
  }
  return (
    <div className="stack">
      <strong>{diagram.title}</strong>
      {error ? <p className="hint">{error}</p> : null}
      {/* Mermaid renders with securityLevel "strict", which sanitizes the SVG. */}
      {svg ? <div className="diagram" dangerouslySetInnerHTML={{ __html: svg }} /> : null}
      <button className="btn-ghost" type="button" onClick={() => setShowCode((value) => !value)}>
        {showCode ? "Hide markup" : "Show markup"}
      </button>
      {showCode || error ? <pre className="code">{diagram.mermaid}</pre> : null}
      <DiagramDownload diagram={diagram} />
    </div>
  );
}

function DiagramDownload({ diagram }: { diagram: InfographicPayload }) {
  const [downloadUrl, setDownloadUrl] = useState("");
  useEffect(() => {
    const url = URL.createObjectURL(new Blob([diagram.mermaid], { type: "text/plain" }));
    setDownloadUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [diagram]);
  return (
    <a
      className="btn-ghost"
      style={{ textAlign: "center" }}
      href={downloadUrl}
      download={`${(diagram.title || "diagram").replace(/[^\w\- ]+/g, "").trim() || "diagram"}.mmd`}
    >
      Download markup
    </a>
  );
}
