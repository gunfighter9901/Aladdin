import { useState } from "react";
import type { FormEvent } from "react";
import type { CollectionInfo, Source } from "../../types";

type Props = {
  sources: Source[];
  collection: CollectionInfo | null;
  busy: boolean;
  highlightedId: string | null;
  onUpload: (form: FormData) => Promise<void>;
  onToggle: (source: Source, active: boolean) => void;
  onDelete: (source: Source) => void;
};

export function SourcePanel({
  sources,
  collection,
  busy,
  highlightedId,
  onUpload,
  onToggle,
  onDelete,
}: Props) {
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const activeCount = sources.filter((source) => source.active && source.status === "ready").length;

  async function submitUrl(event: FormEvent) {
    event.preventDefault();
    const form = new FormData();
    form.set("url", url.trim());
    if (title.trim()) form.set("title", title.trim());
    await onUpload(form);
    setUrl("");
  }

  async function submitText(event: FormEvent) {
    event.preventDefault();
    const form = new FormData();
    form.set("text", text);
    if (title.trim()) form.set("title", title.trim());
    await onUpload(form);
    setText("");
  }

  return (
    <section className="panel" aria-label="Sources">
      <div className="panel-head">
        <div>
          <h2>Sources</h2>
          <p>{activeCount} included in answers. Muted sources are left out of retrieval.</p>
        </div>
      </div>
      <div className="scroll stack">
        <div className="drop">
          <strong>Add a file</strong>
          <span>PDF, Markdown, text, image, or audio. Images and audio are transcribed.</span>
          <input
            type="file"
            aria-label="Upload a study file"
            accept=".pdf,.md,.markdown,.txt,.png,.jpg,.jpeg,.webp,.gif,.wav,.mp3,.m4a,.webm,.ogg,.flac"
            disabled={busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              const form = new FormData();
              form.set("file", file);
              if (title.trim()) form.set("title", title.trim());
              void onUpload(form).finally(() => {
                event.target.value = "";
              });
            }}
          />
        </div>
        <label className="field">
          Optional title
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Lecture 4 notes" />
        </label>
        <form className="stack" onSubmit={submitUrl}>
          <label className="field">
            Web article or YouTube link
            <input
              type="url"
              required
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://"
            />
          </label>
          <button className="btn-ghost" type="submit" disabled={busy || !url.trim()}>
            Add link
          </button>
        </form>
        <form className="stack" onSubmit={submitText}>
          <label className="field">
            Paste notes
            <textarea required value={text} onChange={(event) => setText(event.target.value)} />
          </label>
          <button className="btn-ghost" type="submit" disabled={busy || !text.trim()}>
            Add text
          </button>
        </form>
        {sources.length === 0 ? (
          <div className="empty">
            <strong>Nothing in the library yet.</strong>
            <p>Add a source, then leave it checked if you want the tutor to use it.</p>
          </div>
        ) : (
          sources.map((source) => (
            <article
              key={source.id}
              className={`source${source.active ? " active" : ""}${highlightedId === source.id ? " highlight" : ""}`}
            >
              <div className="source-top">
                <span className={`badge ${source.status}`}>{source.kind}</span>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={source.active}
                    disabled={source.status !== "ready"}
                    onChange={(event) => onToggle(source, event.target.checked)}
                  />
                  Include
                </label>
              </div>
              <h3>{source.title}</h3>
              <div className="meta">
                {source.status === "ready"
                  ? `${source.chunkCount} chunks`
                  : source.status === "error"
                    ? source.error
                    : "Reading…"}
              </div>
              {source.summary ? <p className="summary">{source.summary}</p> : null}
              <div className="source-actions">
                <span className="meta">{source.filename}</span>
                <button className="btn-danger" type="button" onClick={() => onDelete(source)}>
                  Delete
                </button>
              </div>
            </article>
          ))
        )}
        <details className="details">
          <summary>How retrieval is scoped</summary>
          <p>
            One collection{collection ? ` (${collection.name}, ${collection.points} points)` : ""}. Each source is
            isolated by sourceId. Answers search only the checked sources, using a $in filter. An empty selection
            retrieves nothing. Embeddings are local ({collection?.embeddingModel ?? "BAAI/bge-small-en-v1.5"}), with
            binary quantization and on-disk payloads.
          </p>
        </details>
      </div>
    </section>
  );
}
