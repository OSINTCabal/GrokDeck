import type {
  AgentResult,
  BuildCatalog,
  BuildPreview,
  CliFiles,
  CliSession,
  CliSnapshot,
  Conversation,
  Message,
  ModeInfo,
  ProviderInfo,
  ProviderModel,
  Project,
  ServerEvent,
  Settings,
} from "../types";

function errorMessage(body: string, fallback: string) {
  const trimmed = body.trim();
  if (!trimmed) return fallback;
  try {
    const parsed = JSON.parse(trimmed) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.trim()) return parsed.detail;
  } catch {
    /* The body is already plain text. */
  }
  return trimmed.slice(0, 300);
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) {
    throw new Error(errorMessage(await response.text(), response.statusText));
  }
  if (response.status === 204) return undefined as T;
  const ctype = response.headers.get("content-type") || "";
  if (ctype.includes("application/json")) return response.json() as Promise<T>;
  return response.text() as Promise<T>;
}

export interface AccountStatus {
  signedIn: boolean;
  cli: boolean;
  state: "idle" | "waiting" | "signed-in" | "failed";
  message: string;
}

export interface UsageProduct {
  id: string;
  label: string;
  percent: number;
  color: string;
}

export interface UsageReport {
  ok: boolean;
  signedIn: boolean;
  tier: string | null;
  period: "weekly" | "monthly" | null;
  usedPercent: number | null;
  resetsAt: string | null;
  products: UsageProduct[];
  onDemandCap: number | null;
  onDemandUsed: number | null;
  prepaid: number | null;
  cliDataRetentionOptOut: boolean | null;
  error: string | null;
}

export const api = {
  health: () => json<{ ok: boolean; key: boolean; fakeStream: boolean; cli?: boolean; signedIn?: boolean; grokBot?: boolean }>("/api/health"),
  account: () => json<AccountStatus>("/api/account"),
  accountLogin: () => json<AccountStatus>("/api/account/login", { method: "POST" }),
  accountLogout: () => json<AccountStatus>("/api/account/logout", { method: "POST" }),
  accountCancel: () => json<AccountStatus>("/api/account/cancel", { method: "POST" }),
  usage: () => json<UsageReport>("/api/usage"),
  deleteAllConversations: () => json<{ deleted: number }>("/api/conversations/delete-all", { method: "POST" }),
  models: () => json<{ modes: ModeInfo[]; live: boolean }>("/api/models"),
  settings: () => json<Settings>("/api/settings"),
  saveSettings: (body: Partial<Settings>) => json<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  setKey: (key: string) => json<{ configured: boolean }>("/api/key", { method: "PUT", body: JSON.stringify({ key }) }),
  clearKey: () => json<{ configured: boolean }>("/api/key", { method: "DELETE" }),
  providers: (signal?: AbortSignal) => json<{ providers: ProviderInfo[]; models: ProviderModel[]; activeModel: string }>("/api/providers", { signal }),
  saveProvider: (body: Record<string, unknown>) => json<{ providers: ProviderInfo[]; models: ProviderModel[]; activeModel: string }>("/api/providers", { method: "PUT", body: JSON.stringify(body) }),
  deleteProvider: (id: string) => json<{ providers: ProviderInfo[]; models: ProviderModel[]; activeModel: string }>(`/api/providers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  setProviderKey: (id: string, key: string) => json<{ configured: boolean }>(`/api/providers/${encodeURIComponent(id)}/key`, { method: "PUT", body: JSON.stringify({ key }) }),
  clearProviderKey: (id: string) => json<{ configured: boolean }>(`/api/providers/${encodeURIComponent(id)}/key`, { method: "DELETE" }),
  delegateAgents: (body: { task: string; models: string[]; quality: "balanced" | "strong"; effort: string }, signal?: AbortSignal) =>
    json<{ results: AgentResult[] }>("/api/agents/delegate", { method: "POST", body: JSON.stringify(body), signal }),
  conversations: () => json<Conversation[]>("/api/conversations"),
  createConversation: (mode: string) =>
    json<Conversation>("/api/conversations", { method: "POST", body: JSON.stringify({ mode }) }),
  conversation: (id: string) => json<{ conversation: Conversation; messages: Message[] }>(`/api/conversations/${id}`),
  patchConversation: (id: string, body: Record<string, unknown>) =>
    json<Conversation>(`/api/conversations/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteConversation: (id: string) => json(`/api/conversations/${id}`, { method: "DELETE" }),
  branch: (id: string, messageId: string) =>
    json<{ messages: Message[] }>(`/api/conversations/${id}/branch`, {
      method: "POST",
      body: JSON.stringify({ messageId }),
    }),
  feedback: (id: string, messageId: string, value: string | null) =>
    json(`/api/conversations/${id}/feedback`, { method: "POST", body: JSON.stringify({ messageId, value }) }),
  search: (q: string) => json<{ message_id: string; conversation_id: string; title: string; snippet: string }[]>(
    `/api/search?q=${encodeURIComponent(q)}`,
  ),
  projects: () => json<Project[]>("/api/projects"),
  library: () => json<{ id: string; name: string; size: number; mtime: number; kind: "Media" | "Files"; url: string }[]>("/api/library"),
  createProject: (name: string, instructions: string) =>
    json<Project>("/api/projects", { method: "POST", body: JSON.stringify({ name, instructions }) }),
  deleteProject: (id: string) => json(`/api/projects/${id}`, { method: "DELETE" }),
  buildPreview: () => json<BuildPreview>("/api/build/preview", { method: "POST" }),
  buildTurn: (prompt: string) =>
    json<{ ok?: boolean }>("/api/build/turns", { method: "POST", body: JSON.stringify({ prompt }) }),
  buildSessions: () => json<{ sessions: CliSession[]; signedIn: boolean }>("/api/build/sessions"),
  buildOpen: (cwd: string, instructions: string, mode: string) =>
    json<CliSession>("/api/build/sessions", { method: "POST", body: JSON.stringify({ cwd, instructions, mode }) }),
  buildLoad: (id: string, cwd: string) =>
    json<CliSnapshot>(`/api/build/sessions/${encodeURIComponent(id)}/load`, {
      method: "POST",
      body: JSON.stringify({ cwd }),
    }),
  buildFiles: (cwd: string) => json<CliFiles>(`/api/build/files?cwd=${encodeURIComponent(cwd)}`),
  buildCatalog: () => json<BuildCatalog>("/api/build/catalog"),
  buildPermission: (sessionId: string, id: string, allow: boolean) =>
    json<{ ok?: boolean }>(`/api/build/sessions/${encodeURIComponent(sessionId)}/permission`, {
      method: "POST",
      body: JSON.stringify({ id, allow }),
    }),
  buildStop: (sessionId: string) =>
    json<{ ok?: boolean }>(`/api/build/sessions/${encodeURIComponent(sessionId)}/stop`, { method: "POST" }),
  imagine: (prompt: string) =>
    json<{ image: string | null; note: string | null }>("/api/imagine", { method: "POST", body: JSON.stringify({ prompt }) }),
  runCode: (lang: string, code: string) =>
    json<{ ok: boolean; output: string }>("/api/run", { method: "POST", body: JSON.stringify({ lang, code }) }),
  stop: (id: string) => json(`/api/conversations/${id}/stop`, { method: "POST" }),
  exportMarkdown: () => json<string>("/api/export?fmt=md"),
  exportJson: () => json<unknown>("/api/export?fmt=json"),
  importJson: (payload: unknown) => json<{ conversations: number }>("/api/import", { method: "POST", body: JSON.stringify(payload) }),
  transcribe: async (blob: Blob) => {
    const body = new FormData();
    const type = blob.type || "audio/webm";
    const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : type.includes("wav") ? "wav" : "webm";
    body.append("file", new File([blob], `speech.${ext}`, { type: type || "audio/webm" }));
    const response = await fetch("/api/voice/transcribe", { method: "POST", body });
    if (!response.ok) throw new Error(errorMessage(await response.text(), response.statusText));
    const payload = (await response.json()) as { text?: string };
    return payload.text || "";
  },
  speak: async (text: string) => {
    const response = await fetch("/api/voice/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) throw new Error(errorMessage(await response.text(), response.statusText));
    return response.blob();
  },
  upload: async (file: File) => {
    const body = new FormData();
    body.append("file", file);
    const response = await fetch("/api/attachments", { method: "POST", body });
    if (!response.ok) throw new Error(errorMessage(await response.text(), response.statusText));
    return response.json() as Promise<{ id: string; name: string; mime: string }>;
  },
};

function parseEvent(block: string): ServerEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!data.length) return null;
  const payload = JSON.parse(data.join("\n")) as Omit<ServerEvent, "event">;
  return { event, ...payload };
}

export async function streamRequest(
  path: string,
  body: Record<string, unknown>,
  onEvent: (event: ServerEvent) => void | Promise<void>,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error((await response.text()).slice(0, 300));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    pending += decoder.decode(chunk.value, { stream: true });
    const blocks = pending.split("\n\n");
    pending = blocks.pop() || "";
    for (const block of blocks) {
      const event = parseEvent(block);
      if (event) await onEvent(event);
    }
  }
}
