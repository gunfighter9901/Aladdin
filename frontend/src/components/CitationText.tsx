import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation } from "../types";

type Props = {
  text: string;
  citations: Citation[];
  onOpenSource?: (sourceId: string) => void;
};

const CITATION = /\[Source:\s*([^,\]]+?),\s*Chunk:\s*([^\]]+?)\s*\]/g;

export function CitationText({ text, citations, onOpenSource }: Props) {
  const { markdown, refs } = useMemo(() => toMarkdownWithCiteLinks(text, citations), [text, citations]);

  return (
    <div className="answer markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children }) {
            if (href?.startsWith("#cite-")) {
              const index = Number(href.slice("#cite-".length));
              const citation = Number.isFinite(index) ? refs[index] : null;
              if (citation && onOpenSource) {
                const target = citation;
                return (
                  <button
                    type="button"
                    className="cite"
                    onClick={() => onOpenSource(target.sourceId)}
                    title={`Open ${target.filename}`}
                  >
                    {children}
                  </button>
                );
              }
              return <span className="cite-plain">{children}</span>;
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}

function findCitation(citations: Citation[], filename: string, chunkId: string) {
  const exact = citations.find((item) => item.filename === filename && item.chunkId === chunkId);
  if (exact) return exact;
  const lowerFilename = filename.toLowerCase();
  const lowerChunk = chunkId.toLowerCase();
  const caseInsensitive = citations.find(
    (item) => item.filename.toLowerCase() === lowerFilename && item.chunkId.toLowerCase() === lowerChunk,
  );
  if (caseInsensitive) return caseInsensitive;
  // chunkIds are globally unique (full source uuid + index), so a chunkId-only
  // match still resolves even if the filename was edited after ingestion.
  return citations.find((item) => item.chunkId === chunkId || item.chunkId.toLowerCase() === lowerChunk);
}

function toMarkdownWithCiteLinks(text: string, citations: Citation[]) {
  const refs: Array<Citation | null> = [];
  const markdown = text.replace(CITATION, (_full, rawFilename: string, rawChunkId: string) => {
    const filename = (rawFilename ?? "").trim() || "source";
    const chunkId = (rawChunkId ?? "").trim();
    refs.push(findCitation(citations, filename, chunkId) ?? null);
    const label = `${filename} · ${chunkId}`.replace(/[\[\]]/g, "").slice(0, 120);
    return `[${label}](#cite-${refs.length - 1})`;
  });
  return { markdown, refs };
}
