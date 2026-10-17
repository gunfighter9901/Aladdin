export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";

export const MAX_MESSAGE_LENGTH = 8000;
export const MAX_TITLE_LENGTH = 180;
export const MAX_FOCUS_LENGTH = 2000;
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
export const MAX_PASTED_CHARS = 200_000;
export const JSON_TIMEOUT_MS = 30_000;

export const ACCEPTED_EXTENSIONS = [
  ".pdf",
  ".md",
  ".markdown",
  ".txt",
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".gif",
  ".wav",
  ".mp3",
  ".m4a",
  ".webm",
  ".ogg",
  ".flac",
] as const;

export function apiPath(path: string): string {
  return `${API_BASE}${path}`;
}
