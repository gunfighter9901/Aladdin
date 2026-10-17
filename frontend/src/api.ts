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
import { JSON_TIMEOUT_MS, apiPath } from "./config";

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function parseError(response: Response): Promise<{ message: string; code?: string }> {
  try {
    const body = (await response.json()) as { detail?: unknown; code?: unknown };
    const code = typeof body.code === "string" ? body.code : undefined;
    if (typeof body.detail === "string") return { message: body.detail, code };
    if (Array.isArray(body.detail)) {
      return { message: "That request was not valid.", code: code ?? "validation_error" };
    }
    if (body.detail) return { message: "The request could not be completed.", code };
  } catch {
    /* The body was not JSON. */
  }
  return { message: response.statusText || "The request failed." };
}

function withTimeout(signal?: AbortSignal, ms: number = JSON_TIMEOUT_MS): { signal: AbortSignal; cancel: () => void } {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(new DOMException("Request timed out", "TimeoutError")), ms);
  const cancel = () => window.clearTimeout(timer);
  if (signal) {
    if (signal.aborted) controller.abort(signal.reason);
    else signal.addEventListener("abort", () => controller.abort(signal.reason), { once: true });
  }
  return { signal: controller.signal, cancel };
}

export async function api<T>(path: string, init?: RequestInit, timeoutMs?: number): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  // Uploads and streams manage their own lifetime; only plain JSON calls time out here.
  const needsTimeout = timeoutMs !== 0 && !(init?.body instanceof FormData) && !init?.signal;
  const timeout = needsTimeout ? withTimeout(undefined, timeoutMs ?? JSON_TIMEOUT_MS) : null;
  let response: Response;
  try {
    response = await fetch(apiPath(path), { ...init, headers, signal: timeout?.signal ?? init?.signal });
  } catch (error) {
    timeout?.cancel();
    if (error instanceof DOMException && (error.name === "AbortError" || error.name === "TimeoutError")) {
      throw new ApiError(408, "The request timed out. Try again in a moment.", "timeout");
    }
    throw error;
  } finally {
    timeout?.cancel();
  }
  if (!response.ok) {
    const { message, code } = await parseError(response);
    throw new ApiError(response.status, message, code);
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
  return api<Source>(`/api/sources/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteSource(id: string) {
  return api<void>(`/api/sources/${encodeURIComponent(id)}`, { method: "DELETE" });
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
  return api<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`);
}

export function deleteSession(id: string) {
  return api<void>(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
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
  const response = await fetch(apiPath("/api/chat/stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    const { message, code } = await parseError(response);
    throw new ApiError(response.status, message, code);
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
  // SSE frames end with a blank line; servers may use \n\n or \r\n\r\n.
  const normalized = buffer.replace(/\r\n/g, "\n");
  const chunks = normalized.split("\n\n");
  const rest = chunks.pop() ?? "";
  for (const chunk of chunks) {
    if (!chunk.trim() || chunk.trim().startsWith(":")) continue;
    let event = "message";
    const dataLines: string[] = [];
    for (const line of chunk.split("\n")) {
      if (line.startsWith(":")) continue; // heartbeat comment
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
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
