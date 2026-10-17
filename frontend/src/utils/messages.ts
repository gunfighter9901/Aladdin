import { ApiError } from "../api";
import type { Artifacts, ChatMessage } from "../types";

export function messageFrom(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof DOMException && error.name === "AbortError") return fallback;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export function artifactsFromSession(detail: {
  messages: ChatMessage[];
  artifacts: Array<{ kind: string; payload: Artifacts[keyof Artifacts] }>;
}): Artifacts {
  const studio: Artifacts = {};
  for (const message of detail.messages) {
    Object.assign(studio, message.artifacts);
  }
  for (const artifact of detail.artifacts) {
    if (artifact.kind === "podcast") {
      studio.podcast = artifact.payload as Artifacts["podcast"];
    } else if (artifact.kind === "quiz") {
      studio.quiz = artifact.payload as Artifacts["quiz"];
    } else if (artifact.kind === "infographic") {
      studio.infographic = artifact.payload as Artifacts["infographic"];
    }
  }
  return studio;
}
