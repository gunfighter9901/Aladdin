import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation } from "../types";
import { toMarkdownWithCiteLinks } from "../utils/citations";

type Props = {
  text: string;
  citations: Citation[];
  onOpenSource?: (sourceId: string) => void;
};

function safeUrl(url: string): string {
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol === "http:" || parsed.protocol === "https:" || url.startsWith("#cite-")) {
      return url;
    }
  } catch {
    if (url.startsWith("#cite-")) return url;
  }
  return "#blocked";
}

export function CitationText({ text, citations, onOpenSource }: Props) {
  const { markdown, refs } = useMemo(() => toMarkdownWithCiteLinks(text, citations), [text, citations]);

  return (
    <div className="answer markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeUrl}
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
              <a href={href} target="_blank" rel="noreferrer noopener">
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
