import type { ReactNode } from "react";
import type { Citation } from "../types";

type Props = {
  text: string;
  citations: Citation[];
  onOpenSource?: (sourceId: string) => void;
};

const CITATION = /\[Source:\s*([^,\]]+?),\s*Chunk:\s*([^\]]+?)\s*\]/g;

export function CitationText({ text, citations, onOpenSource }: Props) {
  const nodes = renderChunks(text, citations, onOpenSource);
  return <div className="answer">{nodes}</div>;
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

function renderChunks(
  text: string,
  citations: Citation[],
  onOpenSource?: (sourceId: string) => void,
) {
  const nodes: ReactNode[] = [];
  const pattern = new RegExp(CITATION);
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    const index = match.index;
    if (index > last) nodes.push(text.slice(last, index));
    const filename = match[1]?.trim() ?? "source";
    const chunkId = match[2]?.trim() ?? "";
    const citation = findCitation(citations, filename, chunkId);
    const actionable = Boolean(citation && onOpenSource);
    nodes.push(
      actionable ? (
        <button
          key={`${index}-${chunkId}`}
          type="button"
          className="cite"
          onClick={() => citation && onOpenSource?.(citation.sourceId)}
          title={`Open ${citation?.filename}`}
        >
          {filename} · {chunkId}
        </button>
      ) : (
        <span key={`${index}-${chunkId}`} className="cite-plain" title={filename}>
          {filename} · {chunkId}
        </span>
      ),
    );
    last = index + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}
