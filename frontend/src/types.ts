export type SourceStatus = "processing" | "ready" | "error" | string;

export type Source = {
  id: string;
  filename: string;
  title: string;
  kind: string;
  origin: string;
  summary: string;
  chunkCount: number;
  active: boolean;
  status: SourceStatus;
  error: string | null;
  createdAt: string;
};

export type CollectionInfo = {
  name: string;
  points: number;
  embeddingModel: string;
  isolation: string;
  quantization: string;
  payload: string;
};

export type StudySession = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
};

export type Citation = {
  sourceId: string;
  filename: string;
  chunkId: string;
  pageNumber?: number | null;
};

export type TraceStep = {
  type: "status" | "tool_call" | "tool_result" | "response";
  name?: string | null;
  text?: string | null;
  args?: unknown;
  result?: unknown;
};

export type QuizQuestion = {
  id: string;
  question: string;
  options: string[];
  correctAnswer: string;
  rationale: string;
};

export type Flashcard = {
  id: string;
  front: string;
  back: string;
};

export type QuizPayload = {
  title: string;
  questions: QuizQuestion[];
  flashcards: Flashcard[];
  requestedCount?: number;
  returnedCount?: number;
};

export type PodcastTurn = {
  speaker: "Joe" | "Jane" | string;
  text: string;
};

export type PodcastPayload = {
  title: string;
  script: string;
  turns: PodcastTurn[];
  audioBase64: string;
  mimeType: string;
  sampleRateHz: number;
  voices?: { Joe: string; Jane: string };
  audioError?: string | null;
  truncated?: boolean;
};

export type InfographicPayload = {
  title: string;
  diagramType: DiagramType;
  mermaid: string;
};

export type Artifacts = {
  podcast?: PodcastPayload;
  quiz?: QuizPayload;
  infographic?: InfographicPayload;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | string;
  content: string;
  citations: Citation[];
  trace: TraceStep[];
  artifacts: Artifacts;
  createdAt: string;
};

export type SessionDetail = {
  session: StudySession;
  messages: ChatMessage[];
  artifacts: Array<{
    id: string;
    kind: string;
    title: string;
    payload: PodcastPayload | QuizPayload | InfographicPayload;
    createdAt: string;
  }>;
};

export type Health = {
  status: string;
  geminiConfigured: boolean;
  opikConfigured: boolean;
  collection: string;
  qdrant: string;
};

export type DiagramType = "mindmap" | "flowchart" | "timeline" | "sequence";
