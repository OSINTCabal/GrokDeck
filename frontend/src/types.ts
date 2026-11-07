export type ModeId = "fast" | "auto" | "expert" | "heavy";
export type ThemeName =
  | "light"
  | "dark"
  | "system"
  | "grokbot"
  | "slate"
  | "paper"
  | "ocean"
  | "forest";

export interface Conversation {
  id: string;
  title: string;
  pinned: number;
  mode: string;
  model?: string | null;
  project_id: string | null;
  updated_at: number;
  group?: string;
}

export interface Attachment {
  id: string;
  name: string;
  mime: string;
}

export interface Message {
  id: string;
  role: string;
  content: string;
  reasoning: string;
  status: string;
  parent_id: string | null;
  sibling_index?: number;
  sibling_count?: number;
  sibling_ids?: string[];
  citations?: string[];
  feedback?: string | null;
  attachments?: Attachment[];
}

export interface Project {
  id: string;
  name: string;
  instructions: string;
  path?: string | null;
}

export interface ModeInfo {
  id: ModeId;
  label: string;
  model: string;
  effort: string | null;
  fallback: boolean;
}

export interface Settings {
  theme: ThemeName;
  mode: ModeId;
  customInstructions: string;
  buildCli: boolean;
  autoApprove: boolean;
  allowFs: boolean;
  allowTerminal: boolean;
  defaultCwd: string;
  search: boolean;
  activeModel: string;
  subagentPolicy: "off" | "requested" | "auto";
  subagentQuality: "balanced" | "strong";
  subagentWorkers: number;
}

export interface ProviderModel {
  id: string;
  label: string;
  provider: string;
  model: string;
}

export interface ProviderInfo {
  id: string;
  label: string;
  protocol: "grok" | "openai" | "anthropic" | "codex";
  apiBase?: string;
  models: string[];
  keyRequired: boolean;
  configured: boolean;
  keyConfigured: boolean;
}

export interface AgentResult {
  provider: string;
  model: string;
  status: "completed" | "failed";
  text?: string;
  reasoning?: string;
  detail?: string;
}

export interface BuildPreview {
  live: boolean;
  note: string;
  cwd: string;
  steps: { id: string; label: string; state: string }[];
  files: { path: string; status: string }[];
  diff: string;
  terminal: string;
}

export interface CliSession {
  id: string;
  title: string;
  cwd: string;
  updatedAt: number | string;
  models?: CliModel[];
  model?: string;
  effort?: string;
  efforts?: string[];
  commands?: CliCommand[];
}

export interface CliTool {
  id: string;
  name: string;
  path: string;
  readOnly: boolean;
  status: string;
}

export interface CliTurn {
  id: string;
  role: string;
  text: string;
  reasoning: string;
  tools: CliTool[];
  verbose: string;
}

export interface CliCommand {
  name: string;
  description?: string;
}

export interface CliModel {
  id: string;
  name?: string;
  modelId?: string;
  _meta?: { reasoningEfforts?: string[]; reasoning_efforts?: string[] };
}

export interface CliSnapshot {
  session: CliSession;
  messages: CliTurn[];
  models: (string | CliModel)[];
  model?: string;
  effort?: string;
  efforts?: string[];
  commands: (string | CliCommand)[];
}

export interface CliFiles {
  cwd: string;
  files: { path: string; status: string }[];
  diff: string;
  branch?: string;
}

export interface BuildCatalog {
  plugins: string[];
  skills: string[];
}

export interface ServerEvent {
  event: string;
  text?: string;
  url?: string;
  message?: string;
  status?: string;
  userId?: string;
  assistantId?: string;
  conversationId?: string;
  /* Grok Build stream fields. All optional; the chat stream never sends them. */
  id?: string;
  detail?: string;
  verbose?: string;
  turn?: CliTurn;
  tool?: CliTool;
  snapshot?: CliSnapshot;
  commands?: CliCommand[];
}

declare global {
  interface Window {
    grokdeck?: {
      minimize: () => void;
      toggleMaximize: () => void;
      close: () => void;
      notify: (title: string, body: string) => void;
      platform: string;
      pickDirectory?: () => Promise<string | null>;
      openGrokBot?: () => Promise<boolean>;
      resizeStart?: () => void;
      resizeBy?: (edge: string, dx: number, dy: number) => void;
      resizeEnd?: () => void;
      moveStart?: () => void;
      moveBy?: (dx: number, dy: number) => void;
      moveEnd?: () => void;
      setBackground?: (color: string) => Promise<void>;
      grokWeb?: {
        newChat: () => Promise<boolean>;
        open: (bounds: DOMRectInit, theme: string) => Promise<boolean>;
        resize: (bounds: DOMRectInit) => Promise<boolean>;
        close: () => Promise<boolean>;
        refresh: () => Promise<boolean>;
        signOut: () => Promise<boolean>;
        onState: (listener: (state: { status: string; detail: string }) => void) => () => void;
      };
    };
  }
}
