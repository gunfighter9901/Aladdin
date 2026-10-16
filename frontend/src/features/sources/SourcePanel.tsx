import { useState } from "react";
import type { FormEvent } from "react";
import type { CollectionInfo, Source } from "../../types";

type Props = {
  sources: Source[];
  collection: CollectionInfo | null;
  busy: boolean;
  highlightedId: string | null;
  collapsed: boolean;
  onToggleCollapse: () => void;
  onUpload: (form: FormData) => Promise<void>;
  onToggle: (source: Source, active: boolean) => void;
  onDelete: (source: Source) => void;
};

export function SourcePanel({
  sources,
  collection,
  busy,
  highlightedId,
  collapsed,
  onToggleCollapse,
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

  if (collapsed) {
    return (
      <section className="panel src is-collapsed" aria-label="Sources">
        <button
          className="btn-ghost src-expand"
          type="button"
          onClick={onToggleCollapse}
          aria-label="Expand sources"
          title="Expand sources"
        >
          »
        </button>
      </section>
    );
  }

  return (
    <section className="panel src" aria-label="Sources">
      <header className="src-head">
        <div className="src-heading">
          <h2>Sources</h2>
          <p>
            {activeCount} included in answers. Muted sources are left out of retrieval.
          </p>
        </div>
        <button
          className="btn-ghost src-collapse"
          type="button"
          onClick={onToggleCollapse}
          aria-expanded="true"
          aria-label="Minimize sources"
          title="Minimize sources"
        >
          «
        </button>
      </header>

      <div className="src-body">
        <div className="src-add">
          <label className="src-file">
            <span className="src-file-btn">Choose file</span>
            <span className="src-file-hint">PDF, Markdown, text, image, or audio</span>
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
          </label>
          <label className="field">
            Optional title
            <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Lecture 4 notes" />
          </label>
          <form className="src-form" onSubmit={submitUrl}>
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
          <form className="src-form" onSubmit={submitText}>
            <label className="field">
              Paste notes
              <textarea required value={text} onChange={(event) => setText(event.target.value)} />
            </label>
            <button className="btn-ghost" type="submit" disabled={busy || !text.trim()}>
              Add text
            </button>
          </form>
        </div>

        <div className="src-list" role="list">
          {sources.length === 0 ? (
            <div className="empty">
              <strong>Nothing in the library yet.</strong>
              <p>Add a source, then leave it checked if you want the tutor to use it.</p>
            </div>
          ) : (
            sources.map((source) => (
              <article
                key={source.id}
                role="listitem"
                className={`src-card${source.active ? " is-active" : ""}${highlightedId === source.id ? " is-highlight" : ""}`}
              >
                <div className="src-card-top">
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
                <h3 className="src-name">{source.title}</h3>
                <div className="src-meta">
                  {source.status === "ready"
                    ? `${source.chunkCount} chunks`
                    : source.status === "error"
                      ? source.error || "Could not read this source."
                      : "Reading…"}
                </div>
                {source.summary ? <p className="src-summary">{source.summary}</p> : null}
                <div className="src-foot">
                  <span className="src-file-name">{source.filename}</span>
                  <button className="btn-danger src-delete" type="button" onClick={() => onDelete(source)}>
                    Delete
                  </button>
                </div>
              </article>
            ))
          )}
        </div>

        <footer className="src-foot-note">
          <details className="details">
            <summary>How retrieval is scoped</summary>
            <p>
              One collection{collection ? ` (${collection.name}, ${collection.points} points)` : ""}. Each source is
              isolated by sourceId. Answers search only the checked sources, using a $in filter. An empty selection
              retrieves nothing. Embeddings are local ({collection?.embeddingModel ?? "BAAI/bge-small-en-v1.5"}), with
              binary quantization and on-disk payloads.
            </p>
          </details>
        </footer>
      </div>
    </section>
  );
}
