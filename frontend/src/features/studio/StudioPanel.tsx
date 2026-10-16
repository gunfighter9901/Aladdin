import { useEffect, useState } from "react";
import mermaid from "mermaid";
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
    let url = "";
    try {
      const binary = atob(podcast.audioBase64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
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
    </div>
  );
}

let mermaidInitialized = false;

function ensureMermaid() {
  if (!mermaidInitialized) {
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "dark" });
    mermaidInitialized = true;
  }
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
    let cancelled = false;
    ensureMermaid();
    const id = `diagram-${Math.random().toString(36).slice(2)}`;
    mermaid
      .render(id, diagram.mermaid)
      .then((result) => {
        if (!cancelled) {
          setSvg(result.svg);
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
      {svg ? <div className="diagram" dangerouslySetInnerHTML={{ __html: svg }} /> : null}
      <button className="btn-ghost" type="button" onClick={() => setShowCode((value) => !value)}>
        {showCode ? "Hide markup" : "Show markup"}
      </button>
      {showCode || error ? <pre className="code">{diagram.mermaid}</pre> : null}
    </div>
  );
}
