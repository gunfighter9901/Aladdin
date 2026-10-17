import type { Citation } from "../types";

export const CITATION_PATTERN = /\[Source:\s*([^,\]]+?),\s*Chunk:\s*([^\]]+?)\s*\]/g;

export function findCitation(citations: Citation[], filename: string, chunkId: string): Citation | null {
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
  return citations.find((item) => item.chunkId === chunkId || item.chunkId.toLowerCase() === lowerChunk) ?? null;
}

export function toMarkdownWithCiteLinks(text: string, citations: Citation[]) {
  const refs: Array<Citation | null> = [];
  // Fresh regex per call: the shared pattern is global and stateful.
  const pattern = new RegExp(CITATION_PATTERN.source, "g");
  const markdown = text.replace(pattern, (_full, rawFilename: string, rawChunkId: string) => {
    const filename = (rawFilename ?? "").trim() || "source";
    const chunkId = (rawChunkId ?? "").trim();
    refs.push(findCitation(citations, filename, chunkId));
    const label = `${filename} · ${chunkId}`.replace(/[\[\]]/g, "").slice(0, 120);
    return `[${label}](#cite-${refs.length - 1})`;
  });
  return { markdown, refs };
}
