import { ACCEPTED_EXTENSIONS, MAX_PASTED_CHARS, MAX_UPLOAD_BYTES } from "../config";

export function checkFile(file: File): string | null {
  if (file.size === 0) return "That file is empty.";
  if (file.size > MAX_UPLOAD_BYTES) {
    return `That file is larger than ${Math.round(MAX_UPLOAD_BYTES / (1024 * 1024))} MB.`;
  }
  const name = file.name.toLowerCase();
  const ok = ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
  if (!ok) return "Upload a PDF, Markdown, text, image, or audio file.";
  return null;
}

export function checkPastedText(text: string): string | null {
  if (!text.trim()) return "Paste some notes first.";
  if (text.length > MAX_PASTED_CHARS) return "Pasted text is too long. Split it into smaller notes.";
  return null;
}

export function checkUrl(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return "Paste a link first.";
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "Only public http and https links are allowed.";
    }
  } catch {
    return "That does not look like a link.";
  }
  return null;
}
