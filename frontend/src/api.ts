import type {
  Citation,
  CollectionInfo,
  DiagramType,
  Health,
  InfographicPayload,
  PodcastPayload,
  QuizPayload,
  SessionDetail,
  Source,
  StudySession,
  TraceStep,
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail) return "The request could not be completed.";
  } catch {
    /* The body was not JSON. */
  }
  return response.statusText || "The request failed.";
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    throw new ApiError(response.status, await parseError(response));
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function getHealth() {
  return api<Health>("/api/health");
}

export function listSources() {
  return api<Source[]>("/api/sources");
}

export function getCollection() {
  return api<CollectionInfo>("/api/collection");
}

export function uploadSource(form: FormData) {
  return api<Source>("/api/upload-source", { method: "POST", body: form });
}

export function updateSource(id: string, body: { active?: boolean; title?: string }) {
  return api<Source>(`/api/sources/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteSource(id: string) {
  return api<void>(`/api/sources/${id}`, { method: "DELETE" });
}

export function listSessions() {
  return api<StudySession[]>("/api/sessions");
}

export function createSession(title?: string) {
  return api<StudySession>("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export function getSession(id: string) {
  return api<SessionDetail>(`/api/sessions/${id}`);
}

export function deleteSession(id: string) {
  return api<void>(`/api/sessions/${id}`, { method: "DELETE" });
}

export type StreamDone = {
  sessionId: string;
  messageId: string;
  content: string;
  citations: Citation[];
  trace: TraceStep[];
  artifacts: import("./types").Artifacts;
};

export type StreamEvent =
  | { event: "status"; data: { text: string } }
  | { event: "trace"; data: TraceStep }
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: StreamDone }
  | { event: "error"; data: { message: string; status?: number } };

export async function streamChat(
  body: { sessionId: string; message: string; activeSourceIds: string[] },
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, await parseError(response));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = drainSse(buffer, onEvent);
  }
  buffer += decoder.decode();
  drainSse(`${buffer}\n\n`, onEvent);
}

function drainSse(buffer: string, onEvent: (event: StreamEvent) => void) {
  const chunks = buffer.split("\n\n");
  const rest = chunks.pop() ?? "";
  for (const chunk of chunks) {
    if (!chunk.trim()) continue;
    let event = "message";
    const dataLines: string[] = [];
    for (const line of chunk.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) continue;
    let data: StreamEvent["data"];
    try {
      data = JSON.parse(dataLines.join("\n")) as StreamEvent["data"];
    } catch {
      // A single malformed SSE frame must not kill the whole stream.
      continue;
    }
    if (event !== "status" && event !== "trace" && event !== "token" && event !== "done" && event !== "error") {
      continue;
    }
    onEvent({ event, data } as StreamEvent);
  }
  return rest;
}

export function generatePodcast(body: { sourceIds: string[]; sessionId?: string; focus?: string }) {
  return api<PodcastPayload>("/api/generate-podcast", { method: "POST", body: JSON.stringify(body) });
}

export function generateQuiz(body: {
  sourceIds: string[];
  sessionId?: string;
  focus?: string;
  count?: number;
}) {
  return api<QuizPayload>("/api/generate-quiz", { method: "POST", body: JSON.stringify(body) });
}

export function generateInfographic(body: {
  sourceIds: string[];
  sessionId?: string;
  focus?: string;
  diagramType?: DiagramType;
}) {
  return api<InfographicPayload>("/api/generate-infographic", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
