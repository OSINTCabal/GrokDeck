import { create } from "zustand";
import { api, streamRequest, type AccountStatus } from "./lib/api";
import { asksForWorkers } from "./lib/workerRequest";
import type { Attachment, BuildPreview, Conversation, Message, ModeId, ModeInfo, Project, Settings, ThemeName } from "./types";

interface DeckState {
  theme: ThemeName;
  mode: ModeId;
  searchOn: boolean;
  sidebarOpen: boolean;
  settingsOpen: boolean;
  searchOpen: boolean;
  keyConfigured: boolean;
  cli: boolean;
  grokBot: boolean;
  account: AccountStatus | null;
  privateOn: boolean;
  hiddenIds: string[];
  liveModels: boolean;
  modes: ModeInfo[];
  settings: Settings | null;
  conversations: Conversation[];
  projects: Project[];
  activeId: string | null;
  messages: Message[];
  streaming: boolean;
  pending: Attachment[];
  error: string;
  build: BuildPreview | null;
  load: () => Promise<void>;
  openConversation: (id: string | null) => Promise<void>;
  send: (text: string) => Promise<void>;
  edit: (messageId: string, text: string) => Promise<void>;
  regenerate: (messageId: string) => Promise<void>;
  stop: () => Promise<void>;
  setMode: (mode: ModeId) => void;
  setTheme: (theme: ThemeName) => Promise<void>;
  setSearchOn: (on: boolean) => void;
  toggleSidebar: () => void;
  setSettingsOpen: (open: boolean) => void;
  setSearchOpen: (open: boolean) => void;
  setPrivate: (on: boolean) => void;
  addFiles: (files: File[]) => Promise<void>;
  removePending: (id: string) => void;
  rename: (id: string, title: string) => Promise<void>;
  pin: (id: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  branch: (messageId: string) => Promise<void>;
  feedback: (messageId: string, value: "up" | "down") => Promise<void>;
  newChat: () => void;
  refreshBuild: () => Promise<void>;
  saveSettings: (patch: Partial<Settings>) => Promise<void>;
  setKey: (key: string) => Promise<void>;
  clearKey: () => Promise<void>;
  refreshAccount: () => Promise<void>;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  cancelSignIn: () => Promise<void>;
}

let inflight: AbortController | null = null;

function readHidden(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem("grokdeck.private") || "[]");
    return Array.isArray(raw) ? raw.filter((id) => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function hideConversation(id: string) {
  const next = [...new Set([...readHidden(), id])];
  localStorage.setItem("grokdeck.private", JSON.stringify(next));
  return next;
}

export async function workerContext(text: string, settings: Settings | null, signal?: AbortSignal) {
  if (!settings || settings.subagentPolicy === "off" || !asksForWorkers(text, settings.subagentPolicy === "auto")) return "";
  const catalog = await api.providers(signal);
  const lowered = text.toLowerCase();
  const providerPhrase = text.match(/\b(?:use|send|ask|delegate|with)\s+(.{1,100}?)\s+(?:subagents?|agents?|workers?)\b/i)?.[1]?.toLowerCase() ?? lowered;
  const mentioned = catalog.providers
    .filter((provider) => {
      const names = [provider.id.toLowerCase(), provider.label.toLowerCase()];
      if (provider.id === "zai") names.push("z.ai");
      return provider.id !== "grok" && names.some((name) => providerPhrase.includes(name));
    });
  const unavailable = mentioned.find((provider) => !provider.configured || (provider.keyRequired && !provider.keyConfigured));
  if (unavailable) throw new Error(`Connect ${unavailable.label} in Settings → Providers before using it as a subagent.`);
  const named = mentioned.map((provider) => provider.id);
  const quality = /\b(strong|high thinking|high effort)\b/i.test(text) ? "strong" : settings.subagentQuality;
  const effort = /\b(xhigh|max(?:imum)?)\b/i.test(text) ? "xhigh" : /\bhigh\b/i.test(text) ? "high" : quality === "strong" ? "high" : "medium";
  const delegated = await api.delegateAgents({ task: text, models: named.length ? named : ["auto"], quality, effort }, signal);
  const completed = delegated.results.filter((result) => result.status === "completed" && result.text);
  if (!completed.length) {
    throw new Error(delegated.results.map((result) => result.detail || "Worker failed.").join(" "));
  }
  return completed.map((result) => `[${result.provider} · ${result.model}]\n${result.text}`).join("\n\n");
}
// Bumped when the user leaves a chat. An in-flight turn must not navigate back.
let viewEpoch = 0;

const THEME_NAMES: readonly ThemeName[] = [
  "light", "dark", "system", "grokbot", "slate", "paper", "ocean", "forest",
];
const NAMED_THEMES = ["grokbot", "slate", "paper", "ocean", "forest"] as const;
const DARK_THEMES = new Set<ThemeName>(["dark", "grokbot", "slate", "ocean", "forest"]);
const PAGE_COLOR: Record<Exclude<ThemeName, "system">, string> = {
  light: "#f9f8f7",
  dark: "#131211",
  grokbot: "#1f1f1f",
  slate: "#1b212c",
  paper: "#f3eee4",
  ocean: "#0d1b2a",
  forest: "#121a14",
};

export function isThemeName(value: string): value is ThemeName {
  return (THEME_NAMES as readonly string[]).includes(value);
}

export function applyTheme(theme: ThemeName) {
  const name = isThemeName(theme) ? theme : "dark";
  const systemDark = matchMedia("(prefers-color-scheme: dark)").matches;
  const dark = name === "system" ? systemDark : DARK_THEMES.has(name);
  const root = document.documentElement;
  for (const id of NAMED_THEMES) root.classList.toggle(id, name === id);
  root.classList.toggle("dark", dark);
  root.classList.toggle("light", !dark);
  localStorage.setItem("grokdeck.theme", name);
  const background = name === "system" ? (systemDark ? PAGE_COLOR.dark : PAGE_COLOR.light) : PAGE_COLOR[name];
  void window.grokdeck?.setBackground?.(background);
}

export const useDeck = create<DeckState>((set, get) => ({
  theme: (() => {
    const saved = localStorage.getItem("grokdeck.theme") || "light";
    return isThemeName(saved) ? saved : "light";
  })(),
  mode: "fast",
  searchOn: false,
  sidebarOpen: false,
  settingsOpen: false,
  searchOpen: false,
  keyConfigured: false,
  cli: false,
  grokBot: false,
  account: null,
  privateOn: false,
  hiddenIds: readHidden(),
  liveModels: false,
  modes: [
    { id: "fast", label: "Fast", model: "grok-4-fast", effort: null, fallback: true },
    { id: "auto", label: "Auto", model: "grok-4", effort: null, fallback: true },
    { id: "expert", label: "Expert", model: "grok-4", effort: "high", fallback: true },
    { id: "heavy", label: "Heavy", model: "grok-4", effort: "high", fallback: true },
  ],
  settings: null,
  conversations: [],
  projects: [],
  activeId: null,
  messages: [],
  streaming: false,
  pending: [],
  error: "",
  build: null,

  load: async () => {
    const [health, settings, conversations, projects, models, account] = await Promise.all([
      api.health(),
      api.settings(),
      api.conversations(),
      api.projects(),
      api.models(),
      api.account(),
    ]);
    const theme = isThemeName(settings.theme) ? settings.theme : "dark";
    applyTheme(theme);
    set({
      keyConfigured: health.key,
      cli: account.cli || !!health.cli,
      grokBot: !!health.grokBot,
      account,
      settings: { ...settings, theme },
      theme,
      mode: settings.mode || "fast",
      searchOn: settings.search,
      conversations,
      projects,
      modes: models.modes,
      liveModels: models.live,
      sidebarOpen: conversations.length > 0,
    });
  },

  openConversation: async (id) => {
    viewEpoch += 1;
    const epoch = viewEpoch;
    inflight?.abort();
    inflight = null;
    set({ streaming: false, pending: [], error: "" });
    if (!id) {
      set({ activeId: null, messages: [] });
      return;
    }
    try {
      const data = await api.conversation(id);
      if (epoch !== viewEpoch) return;
      set({ activeId: id, messages: data.messages, mode: (data.conversation.mode as ModeId) || get().mode });
    } catch (error) {
      if (epoch === viewEpoch) set({ error: error instanceof Error ? error.message : "The chat did not open." });
    }
  },

  newChat: () => {
    viewEpoch += 1;
    inflight?.abort();
    inflight = null;
    set({ activeId: null, messages: [], error: "", pending: [], streaming: false });
  },

  send: async (text) => {
    const trimmed = text.trim();
    if (!trimmed || get().streaming) return;
    const controller = new AbortController();
    const epoch = viewEpoch;
    inflight = controller;
    set({ streaming: true, error: "" });
    let reports = "";
    try {
      reports = await workerContext(trimmed, get().settings, controller.signal);
    } catch (error) {
      if (inflight === controller && viewEpoch === epoch) {
        inflight = null;
        set({ streaming: false, error: error instanceof Error ? error.message : "Subagents could not complete their work." });
      }
      return;
    }
    if (controller.signal.aborted || viewEpoch !== epoch) {
      if (inflight === controller) {
        inflight = null;
        set({ streaming: false });
      }
      return;
    }
    let id = get().activeId;
    try {
      if (!id) {
        const created = await api.createConversation(get().mode);
        if (controller.signal.aborted || viewEpoch !== epoch) return;
        id = created.id;
        const hiddenIds = get().privateOn ? hideConversation(id) : get().hiddenIds;
        set((state) => ({
          activeId: id,
          conversations: [created, ...state.conversations],
          sidebarOpen: true,
          messages: [],
          hiddenIds,
        }));
      }
      await runStream(id, "/turns", {
        text: trimmed,
        mode: get().mode,
        attachmentIds: get().pending.map((file) => file.id),
        ...(reports ? { workerContext: reports } : {}),
      }, controller);
    } catch (error) {
      if (inflight === controller && viewEpoch === epoch) {
        set({ error: error instanceof Error ? error.message : "The turn did not start." });
      }
    } finally {
      if (inflight === controller) {
        inflight = null;
        set({ streaming: false });
      }
    }
  },

  edit: async (messageId, text) => {
    const id = get().activeId;
    if (!id || get().streaming) return;
    await runStream(id, "/edit", { messageId, text, mode: get().mode });
  },

  regenerate: async (messageId) => {
    const id = get().activeId;
    if (!id || get().streaming) return;
    await runStream(id, "/regenerate", { messageId, mode: get().mode });
  },

  stop: async () => {
    const id = get().activeId;
    inflight?.abort();
    inflight = null;
    set({ streaming: false });
    if (id) await api.stop(id).catch(() => undefined);
  },

  setMode: (mode) => {
    set({ mode });
    const id = get().activeId;
    if (id) void api.patchConversation(id, { mode });
    void api.saveSettings({ mode });
  },

  setTheme: async (theme) => {
    applyTheme(theme);
    set({ theme });
    const settings = await api.saveSettings({ theme });
    set({ settings });
  },

  setSearchOn: (searchOn) => {
    set({ searchOn });
    void api.saveSettings({ search: searchOn });
  },

  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
  setSearchOpen: (searchOpen) => set({ searchOpen }),
  setPrivate: (privateOn) => {
    set({ privateOn });
    if (privateOn) get().newChat();
  },

  addFiles: async (files) => {
    const uploaded: Attachment[] = [];
    for (const file of files) {
      const row = await api.upload(file);
      uploaded.push(row);
    }
    set((state) => ({ pending: [...state.pending, ...uploaded] }));
  },

  removePending: (id) => set((state) => ({ pending: state.pending.filter((file) => file.id !== id) })),

  rename: async (id, title) => {
    const row = await api.patchConversation(id, { title });
    set((state) => ({ conversations: state.conversations.map((item) => (item.id === id ? { ...item, ...row } : item)) }));
  },

  pin: async (id) => {
    const current = get().conversations.find((item) => item.id === id);
    const row = await api.patchConversation(id, { pinned: !current?.pinned });
    const conversations = await api.conversations();
    set({ conversations });
    void row;
  },

  remove: async (id) => {
    await api.deleteConversation(id);
    const conversations = await api.conversations();
    const leaving = get().activeId === id;
    set({ conversations, ...(leaving ? { activeId: null, messages: [] } : {}) });
  },

  branch: async (messageId) => {
    const id = get().activeId;
    if (!id) return;
    const data = await api.branch(id, messageId);
    set({ messages: data.messages });
  },

  feedback: async (messageId, value) => {
    const id = get().activeId;
    if (!id) return;
    await api.feedback(id, messageId, value);
    set((state) => ({
      messages: state.messages.map((msg) => (msg.id === messageId ? { ...msg, feedback: value } : msg)),
    }));
  },

  refreshBuild: async () => {
    set({ build: await api.buildPreview() });
  },

  saveSettings: async (patch) => {
    const settings = await api.saveSettings(patch);
    const theme = isThemeName(settings.theme) ? settings.theme : get().theme;
    set({ settings: { ...settings, theme }, theme, mode: settings.mode, searchOn: settings.search });
    applyTheme(theme);
  },

  setKey: async (key) => {
    const result = await api.setKey(key);
    const models = await api.models();
    set({ keyConfigured: result.configured, modes: models.modes, liveModels: models.live });
  },

  clearKey: async () => {
    await api.clearKey();
    set({ keyConfigured: false, liveModels: false });
  },

  refreshAccount: async () => {
    const account = await api.account();
    set({ account, cli: account.cli });
  },

  signIn: async () => {
    const account = await api.accountLogin();
    set({ account, cli: account.cli });
  },

  signOut: async () => {
    const account = await api.accountLogout();
    set({ account, cli: account.cli });
  },

  cancelSignIn: async () => {
    const account = await api.accountCancel();
    set({ account, cli: account.cli });
  },
}));

function stillOnTurn(id: string, epoch: number) {
  return viewEpoch === epoch && useDeck.getState().activeId === id;
}

async function pullThread(id: string, epoch: number) {
  const data = await api.conversation(id);
  if (!stillOnTurn(id, epoch)) return;
  const mode = (data.conversation.mode as ModeId) || useDeck.getState().mode;
  useDeck.setState({ messages: data.messages, mode, error: "" });
}

async function runStream(id: string, suffix: string, body: Record<string, unknown>, existing?: AbortController) {
  const epoch = viewEpoch;
  if (!existing) inflight?.abort();
  const controller = existing || new AbortController();
  inflight = controller;
  useDeck.setState({ streaming: true, error: "", pending: [] });
  let assistantId = "";
  try {
    await streamRequest(`/api/conversations/${id}${suffix}`, body, async (event) => {
      if (controller.signal.aborted || !stillOnTurn(id, epoch)) return;
      if (event.event === "ready" && event.assistantId) {
        assistantId = event.assistantId;
        await pullThread(id, epoch);
      } else if (event.event === "token" && event.text) {
        const text = event.text;
        useDeck.setState((state) => ({
          messages: state.messages.map((msg) =>
            msg.id === assistantId ? { ...msg, content: msg.content + text, status: "streaming" } : msg,
          ),
        }));
      } else if (event.event === "reasoning" && event.text) {
        const text = event.text;
        useDeck.setState((state) => ({
          messages: state.messages.map((msg) =>
            msg.id === assistantId ? { ...msg, reasoning: (msg.reasoning || "") + text } : msg,
          ),
        }));
      } else if (event.event === "citation" && event.url) {
        const url = event.url;
        useDeck.setState((state) => ({
          messages: state.messages.map((msg) =>
            msg.id === assistantId ? { ...msg, citations: [...(msg.citations || []), url] } : msg,
          ),
        }));
      } else if (event.event === "error") {
        useDeck.setState({ error: event.message || "The turn failed." });
      }
    }, controller.signal);
  } catch (error) {
    if (stillOnTurn(id, epoch) && !(error instanceof DOMException && error.name === "AbortError")) {
      useDeck.setState({ error: error instanceof Error ? error.message : "The turn failed." });
    }
  } finally {
    const owns = inflight === controller;
    if (owns) inflight = null;
    const conversations = await api.conversations().catch(() => useDeck.getState().conversations);
    if (!owns || !stillOnTurn(id, epoch)) {
      useDeck.setState({ conversations });
      return;
    }
    useDeck.setState({ streaming: false, conversations });
    await pullThread(id, epoch);
    if (document.hidden && stillOnTurn(id, epoch)) window.grokdeck?.notify("GrokDeck", "Response ready");
  }
}
