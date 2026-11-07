import * as Dialog from "@radix-ui/react-dialog";
import * as Switch from "@radix-ui/react-switch";
import {
  Bot,
  CreditCard,
  Gauge,
  Shield,
  SlidersHorizontal,
  Sparkles,
  Sun,
  UserRound,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, type UsageReport } from "../lib/api";
import { useDeck } from "../state";
import type { ProviderInfo, ProviderModel, Settings, ThemeName } from "../types";

const THEMES: { id: ThemeName; label: string }[] = [
  { id: "system", label: "System" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
  { id: "grokbot", label: "Grok Bot" },
  { id: "slate", label: "Slate" },
  { id: "paper", label: "Paper" },
  { id: "ocean", label: "Ocean" },
  { id: "forest", label: "Forest" },
];

const STYLES: { id: string; label: string; text: string | null }[] = [
  { id: "default", label: "Default", text: "" },
  { id: "concise", label: "Concise", text: "Be concise." },
  { id: "formal", label: "Formal", text: "Use a formal tone." },
  { id: "tutor", label: "Tutor", text: "Explain step by step, like a tutor." },
  { id: "comprehensive", label: "Comprehensive", text: "Be thorough and cover the important details." },
  { id: "custom", label: "Custom", text: null },
];

const PAGES = [
  { id: "account", label: "Account", icon: UserRound },
  { id: "providers", label: "Providers", icon: Bot },
  { id: "appearance", label: "Appearance", icon: Sun },
  { id: "behavior", label: "Behavior", icon: SlidersHorizontal },
  { id: "customize", label: "Customize", icon: Sparkles },
  { id: "data", label: "Data Controls", icon: Shield },
  { id: "usage", label: "Usage", icon: Gauge },
  { id: "billing", label: "Billing", icon: CreditCard },
] as const;

type PageId = (typeof PAGES)[number]["id"];

export function SettingsDialog() {
  const open = useDeck((s) => s.settingsOpen);
  const setSettingsOpen = useDeck((s) => s.setSettingsOpen);
  const [page, setPage] = useState<PageId>("account");

  useEffect(() => {
    if (open) setPage("account");
  }, [open]);

  return (
    <Dialog.Root open={open} onOpenChange={setSettingsOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog settings-shell" aria-describedby={undefined}>
          <nav className="settings-nav" aria-label="Settings">
            {PAGES.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-current={page === item.id ? "page" : undefined}
                  onClick={() => setPage(item.id)}
                >
                  <Icon size={16} />
                  {item.label}
                </button>
              );
            })}
          </nav>
          <div className="settings-main">
            <Dialog.Close className="settings-close" aria-label="Close">
              <X size={16} />
            </Dialog.Close>
            {page === "account" && <AccountPage />}
            {page === "providers" && <ProvidersPage />}
            {page === "appearance" && <AppearancePage />}
            {page === "behavior" && <BehaviorPage />}
            {page === "customize" && <CustomizePage />}
            {page === "data" && <DataPage />}
            {page === "usage" && <UsagePage />}
            {page === "billing" && <BillingPage onUsage={() => setPage("usage")} />}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

type ProviderDraft = {
  id: string;
  label: string;
  protocol: "openai" | "anthropic" | "codex";
  apiBase: string;
  models: string;
  key: string;
};

function draftFrom(provider?: ProviderInfo): ProviderDraft {
  return {
    id: provider?.id || "custom",
    label: provider?.label || "Custom provider",
    protocol: provider?.protocol === "anthropic" ? "anthropic" : provider?.protocol === "codex" ? "codex" : "openai",
    apiBase: provider?.apiBase || "",
    models: provider?.models.join(", ") || "",
    key: "",
  };
}

function ProvidersPage() {
  const settings = useDeck((s) => s.settings);
  const saveSettings = useDeck((s) => s.saveSettings);
  const [rows, setRows] = useState<ProviderInfo[]>([]);
  const [models, setModels] = useState<ProviderModel[]>([]);
  const [active, setActive] = useState("grok:subscription");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<ProviderDraft>(draftFrom());
  const [note, setNote] = useState("");

  function load() {
    return api.providers().then((data) => {
      setRows(data.providers);
      setModels(data.models);
      setActive(data.activeModel);
    });
  }

  useEffect(() => {
    void load().catch(() => setNote("Could not load providers."));
  }, []);

  async function save() {
    const names = draft.models.split(",").map((name) => name.trim()).filter(Boolean);
    try {
      const data = await api.saveProvider({
        id: draft.id,
        label: draft.label,
        protocol: draft.protocol,
        apiBase: draft.apiBase,
        models: names,
        ...(draft.key.trim() ? { key: draft.key.trim() } : {}),
      });
      setRows(data.providers);
      setModels(data.models);
      setActive(data.activeModel);
      setEditing(null);
      setNote(`${draft.label || draft.id} is ready in Chat.`);
    } catch (error) {
      setNote(error instanceof Error ? error.message : "Could not save the provider.");
    }
  }

  async function selectMain(value: string) {
    try {
      await saveSettings({ activeModel: value });
      setActive(value);
      setNote("New chats in the desktop workspace will use this model.");
    } catch (error) {
      setNote(error instanceof Error ? error.message : "Could not change the main model.");
    }
  }

  async function saveWorkers(patch: Partial<Settings>) {
    try {
      await saveSettings(patch);
      setNote("Subagent preferences saved.");
    } catch (error) {
      setNote(error instanceof Error ? error.message : "Could not save subagent preferences.");
    }
  }

  return (
    <>
      <Dialog.Title asChild><h2>Providers</h2></Dialog.Title>
      <p className="note">Connect a provider here, then choose it from the Chat model selector. Grok web keeps its own account and conversations.</p>
      <label className="field provider-main">
        Default desktop chat model
        <select aria-label="Default desktop chat model" value={active} onChange={(event) => void selectMain(event.target.value)}>
          {models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
        </select>
      </label>
      <p className="set-kicker">Subagents</p>
      <SetRow title="Delegation" detail="Runs when you ask for agents in desktop Chat or Build. Automatic also helps with longer review, research, audit, and planning prompts.">
        <select aria-label="Subagent delegation" value={settings?.subagentPolicy || "requested"} onChange={(event) => void saveWorkers({ subagentPolicy: event.target.value as Settings["subagentPolicy"] })}>
          <option value="requested">When I ask</option>
          <option value="auto">Automatic</option>
          <option value="off">Off</option>
        </select>
      </SetRow>
      <SetRow title="Worker quality" detail="Strong chooses higher-tier configured model names where available.">
        <select aria-label="Subagent quality" value={settings?.subagentQuality || "balanced"} onChange={(event) => void saveWorkers({ subagentQuality: event.target.value as Settings["subagentQuality"] })}>
          <option value="balanced">Balanced</option>
          <option value="strong">Strong</option>
        </select>
      </SetRow>
      <SetRow title="Workers per request" detail="Each worker gets one read-only model call and returns a report for the main model to review.">
        <select aria-label="Subagent worker limit" value={settings?.subagentWorkers || 2} onChange={(event) => void saveWorkers({ subagentWorkers: Number(event.target.value) })}>
          <option value={1}>1</option>
          <option value={2}>2</option>
          <option value={3}>3</option>
        </select>
      </SetRow>
      <p className="note">In desktop Chat or Build, try “use DeepSeek and Z.ai subagents with strong models and high thinking.” Named configured services are honored; workers cannot use files, tools, or credentials.</p>
      {rows.map((provider) => (
        <div className="provider-card" key={provider.id}>
          <SetRow
            title={provider.label}
            detail={provider.id === "grok" ? "Uses your Grok CLI or xAI key." : provider.protocol === "codex" ? "Uses the official Codex CLI sign-in." : provider.configured ? provider.keyRequired && !provider.keyConfigured ? "API key still needed." : `${provider.models.length} model${provider.models.length === 1 ? "" : "s"} ready.` : "Not connected."}
          >
            {provider.id !== "grok" && (
              <button type="button" className="pill ghost" onClick={() => {
                setEditing(editing === provider.id ? null : provider.id);
                setDraft(draftFrom(provider));
                setNote("");
              }}>{editing === provider.id ? "Close" : provider.configured ? "Manage" : "Connect"}</button>
            )}
          </SetRow>
          {editing === provider.id && <ProviderForm draft={draft} setDraft={setDraft} onSave={save} onRemove={async () => {
            try {
              const data = await api.deleteProvider(provider.id);
              setRows(data.providers);
              setModels(data.models);
              setActive(data.activeModel);
              setEditing(null);
              setNote(`${provider.label} was removed.`);
            } catch (error) {
              setNote(error instanceof Error ? error.message : "Could not remove the provider.");
            }
          }} onClearKey={provider.keyConfigured ? async () => {
            try {
              await api.clearProviderKey(provider.id);
              await load();
              setNote("API key removed from this computer.");
            } catch (error) {
              setNote(error instanceof Error ? error.message : "Could not remove the API key.");
            }
          } : undefined} removable={provider.configured} />}
        </div>
      ))}
      <div className="provider-card">
        <SetRow title="Custom provider" detail="Connect any compatible OpenAI or Anthropic endpoint.">
          <button type="button" className="pill ghost" onClick={() => {
            setEditing("custom");
            setDraft(draftFrom());
            setNote("");
          }}>Add</button>
        </SetRow>
        {editing === "custom" && <ProviderForm custom draft={draft} setDraft={setDraft} onSave={save} />}
      </div>
      {note && <p className="note" role="status">{note}</p>}
    </>
  );
}

function ProviderForm({ custom = false, draft, setDraft, onSave, onRemove, onClearKey, removable = false }: {
  custom?: boolean;
  draft: ProviderDraft;
  setDraft: (next: ProviderDraft) => void;
  onSave: () => Promise<void>;
  onRemove?: () => Promise<void>;
  onClearKey?: () => Promise<void>;
  removable?: boolean;
}) {
  function update(name: keyof ProviderDraft, value: string) {
    setDraft({ ...draft, [name]: value });
  }
  const codex = draft.protocol === "codex";
  return (
    <div className="provider-form">
      {custom && <label className="field">Provider ID<input aria-label="Provider ID" value={draft.id} onChange={(event) => update("id", event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))} /></label>}
      <label className="field">Name<input aria-label="Provider name" value={draft.label} onChange={(event) => update("label", event.target.value)} /></label>
      <label className="field">API format
        <select aria-label="Provider API format" value={draft.protocol} onChange={(event) => update("protocol", event.target.value)}>
          <option value="openai">OpenAI-compatible</option>
          <option value="anthropic">Anthropic</option>
          <option value="codex">Codex CLI</option>
        </select>
      </label>
      {!codex && <label className="field">API base<input aria-label="Provider API base" value={draft.apiBase} placeholder="https://api.example.com/v1" onChange={(event) => update("apiBase", event.target.value)} /></label>}
      <label className="field">Model IDs<input aria-label="Provider model IDs" value={draft.models} placeholder="model-name, another-model" onChange={(event) => update("models", event.target.value)} /></label>
      {codex ? <p className="note">Run <code>codex login</code> once. GrokDeck uses that secure CLI sign-in and never asks for its token.</p> : <label className="field">API key<input type="password" autoComplete="off" aria-label="Provider API key" placeholder="Paste a key to save or replace it" value={draft.key} onChange={(event) => update("key", event.target.value)} /></label>}
      <div className="row">
        <button type="button" className="pill solid" onClick={() => void onSave()}>Save provider</button>
        {onClearKey && <button type="button" className="pill ghost" onClick={() => void onClearKey()}>Remove key</button>}
        {removable && onRemove && <button type="button" className="pill ghost danger" onClick={() => void onRemove()}>Remove</button>}
      </div>
    </div>
  );
}

function AccountPage() {
  const account = useDeck((s) => s.account);
  const signIn = useDeck((s) => s.signIn);
  const signOut = useDeck((s) => s.signOut);
  const cancelSignIn = useDeck((s) => s.cancelSignIn);
  const keyConfigured = useDeck((s) => s.keyConfigured);
  const setKey = useDeck((s) => s.setKey);
  const clearKey = useDeck((s) => s.clearKey);
  const [usage, setUsage] = useState<UsageReport | null>(null);
  const [key, setKeyDraft] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    let live = true;
    void api.usage().then((row) => {
      if (live) setUsage(row);
    }).catch(() => {
      if (live) setUsage(null);
    });
    return () => {
      live = false;
    };
  }, []);

  const signedIn = !!account?.signedIn;
  const waiting = account?.state === "waiting";
  const plan = usage?.tier || (signedIn ? "Grok" : "Not signed in");
  return (
    <>
      <Dialog.Title asChild><h2>Account</h2></Dialog.Title>
      <div className="account-card">
        <span className={`avatar${waiting ? " wait" : ""}`} aria-hidden>
          {signedIn ? "G" : <UserRound size={16} />}
        </span>
        <div>
          <strong>{waiting ? "Opening your browser…" : signedIn ? "Grok CLI connected" : "Sign in"}</strong>
          <p className="note">{plan}</p>
        </div>
      </div>
      {account?.state === "failed" && account.message && <p className="note" role="status">{account.message}</p>}
      <SetRow title="Subscription" detail={signedIn ? plan : "Sign in with the Grok CLI to use your account."}>
        <a className="pill ghost" href="https://grok.com?_s=usage" target="_blank" rel="noreferrer">Manage</a>
      </SetRow>
      <SetRow
        title={waiting ? "Sign-in" : signedIn ? "Sign out" : "Sign in"}
        detail={waiting ? "Finish in the browser window." : signedIn ? "Signs out of the Grok CLI on this computer." : "Opens your browser through the Grok CLI."}
      >
        {waiting ? (
          <button type="button" className="pill ghost" onClick={() => void cancelSignIn()}>Cancel</button>
        ) : signedIn ? (
          <button type="button" className="pill ghost" onClick={() => void signOut()}>Sign out</button>
        ) : (
          <button type="button" className="pill solid" onClick={() => void signIn()} disabled={account?.cli === false}>
            Sign in
          </button>
        )}
      </SetRow>
      <p className="set-kicker">On this computer</p>
      <label className="field">
        API key
        <input
          type="password"
          autoComplete="off"
          aria-label="API key"
          placeholder={keyConfigured ? "A key is saved. Enter a new one to replace it." : "Paste an xAI API key"}
          value={key}
          onChange={(event) => setKeyDraft(event.target.value)}
        />
      </label>
      <p className="note">
        {keyConfigured
          ? "Key saved on this computer. Image generation uses it."
          : "No key saved. Images need a key. Chat uses your CLI sign-in."}
      </p>
      <div className="row">
        <button
          type="button"
          className="pill ghost"
          disabled={!key.trim()}
          onClick={() => {
            void setKey(key).then(() => {
              setKeyDraft("");
              setNote("Key saved on this machine.");
            }).catch((error: unknown) => {
              setNote(error instanceof Error ? error.message : "Could not save the key.");
            });
          }}
        >
          Save key
        </button>
        <button type="button" className="pill ghost" onClick={() => void clearKey()} disabled={!keyConfigured}>
          Remove key
        </button>
      </div>
      {note && <p className="note" role="status">{note}</p>}
    </>
  );
}

function AppearancePage() {
  const theme = useDeck((s) => s.theme);
  const setTheme = useDeck((s) => s.setTheme);
  return (
    <>
      <Dialog.Title asChild><h2>Appearance</h2></Dialog.Title>
      <div className="theme-cards" role="radiogroup" aria-label="Theme">
        {THEMES.map((item) => (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={theme === item.id}
            data-on={theme === item.id}
            className="theme-card"
            onClick={() => void setTheme(item.id)}
          >
            <span className={`theme-shot${item.id === "system" ? " system" : ""}`} aria-hidden>
              {item.id === "system" ? (
                <>
                  <i className="light" />
                  <i className="dark" />
                </>
              ) : (
                <i className={item.id} />
              )}
            </span>
            <strong>{item.label}</strong>
          </button>
        ))}
      </div>
      <SetRow title="Language" detail="GrokDeck follows the language of this computer.">
        <span className="set-value">English</span>
      </SetRow>
    </>
  );
}

function BehaviorPage() {
  const settings = useDeck((s) => s.settings);
  const saveSettings = useDeck((s) => s.saveSettings);
  const cli = useDeck((s) => s.cli);
  const [cwd, setCwd] = useState(settings?.defaultCwd || "");

  useEffect(() => {
    setCwd(settings?.defaultCwd || "");
  }, [settings?.defaultCwd]);

  async function patch(body: Partial<Settings>) {
    await saveSettings(body);
  }

  return (
    <>
      <Dialog.Title asChild><h2>Behavior</h2></Dialog.Title>
      <Toggle
        title="Search the web"
        detail="Search when a question needs the web. Image generation still uses the API key."
        checked={!!settings?.search}
        onChange={(search) => void patch({ search })}
      />
      <p className="set-kicker">This computer</p>
      <Toggle
        title="Build"
        detail="Let Build drive the local grok CLI. Turning this on does not start a turn."
        checked={!!settings?.buildCli}
        onChange={(buildCli) => void patch({ buildCli })}
      />
      <Toggle
        title="Auto-approve"
        detail="Allow writes and terminal commands without asking first."
        checked={!!settings?.autoApprove}
        onChange={(autoApprove) => void patch({ autoApprove })}
      />
      <Toggle
        title="File reads"
        detail="Let Build read files in the working directory."
        checked={settings?.allowFs !== false}
        onChange={(allowFs) => void patch({ allowFs })}
      />
      <Toggle
        title="Terminal"
        detail="Let Build run terminal commands."
        checked={!!settings?.allowTerminal}
        onChange={(allowTerminal) => void patch({ allowTerminal })}
      />
      <div className="set-block">
        <h3>Working directory</h3>
        <p>Build and terminal commands start here.</p>
        <label className="field">
          Working directory
          <input aria-label="Working directory" value={cwd} onChange={(event) => setCwd(event.target.value)} />
        </label>
        <div className="row">
          {window.grokdeck?.pickDirectory && (
            <button
              type="button"
              className="pill ghost"
              onClick={() => {
                void window.grokdeck?.pickDirectory?.().then((dir) => {
                  if (dir) setCwd(dir);
                });
              }}
            >
              Browse
            </button>
          )}
          <button type="button" className="pill solid" onClick={() => void patch({ defaultCwd: cwd })}>
            Save directory
          </button>
        </div>
        <p className="note">{cli ? "Grok CLI is installed." : "Grok CLI was not found on PATH."}</p>
      </div>
    </>
  );
}

function CustomizePage() {
  const settings = useDeck((s) => s.settings);
  const saveSettings = useDeck((s) => s.saveSettings);
  const projects = useDeck((s) => s.projects);
  const [instructions, setInstructions] = useState(settings?.customInstructions || "");
  const [projectName, setProjectName] = useState("");
  const selected = styleId(settings?.customInstructions || "");

  useEffect(() => {
    setInstructions(settings?.customInstructions || "");
  }, [settings?.customInstructions]);

  return (
    <>
      <Dialog.Title asChild><h2>Customize</h2></Dialog.Title>
      <p className="set-kicker">Response style</p>
      <div className="style-picks" role="radiogroup" aria-label="Response style">
        {STYLES.map((item) => (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={selected === item.id}
            onClick={() => {
              if (item.text == null) return;
              setInstructions(item.text);
              void saveSettings({ customInstructions: item.text });
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
      {selected === "custom" && (
        <>
          <label className="field">
            Custom instructions
            <textarea
              aria-label="Custom instructions"
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              placeholder="How should Grok respond?"
            />
          </label>
          <div className="row">
            <button
              type="button"
              className="pill solid"
              onClick={() => void saveSettings({ customInstructions: instructions })}
            >
              Save instructions
            </button>
          </div>
        </>
      )}
      <p className="set-kicker">Projects</p>
      <div className="field">
        <div className="row">
          <input aria-label="Project name" placeholder="Project name" value={projectName} onChange={(event) => setProjectName(event.target.value)} />
          <button
            type="button"
            className="pill ghost"
            onClick={() => {
              const name = projectName.trim();
              if (!name) return;
              void api.createProject(name, "").then((project) => {
                useDeck.setState((state) => ({ projects: [project, ...state.projects] }));
                setProjectName("");
              });
            }}
          >
            Create
          </button>
        </div>
        {projects.length === 0 && <p className="note">No projects yet.</p>}
        {projects.map((project) => (
          <SetRow key={project.id} title={project.name}>
            <button
              type="button"
              className="pill ghost"
              onClick={() => {
                void api.deleteProject(project.id).then(() => {
                  useDeck.setState((state) => ({ projects: state.projects.filter((item) => item.id !== project.id) }));
                });
              }}
            >
              Delete
            </button>
          </SetRow>
        ))}
      </div>
    </>
  );
}

function DataPage() {
  const [usage, setUsage] = useState<UsageReport | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    let live = true;
    void api.usage().then((row) => {
      if (live) setUsage(row);
    }).catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  const optedOut = usage?.cliDataRetentionOptOut;
  const training = optedOut == null ? "This sign-in did not report a training choice." : optedOut
    ? "New conversations are not used to train the model. grok.com keeps its own switch."
    : "New conversations can be used to train the model. grok.com keeps its own switch.";
  return (
    <>
      <Dialog.Title asChild><h2>Data Controls</h2></Dialog.Title>
      <SetRow title="Improve the Model" detail={training}>
        {optedOut == null ? (
          <span className="set-value">Not reported</span>
        ) : (
          <Switch.Root className="switch" checked={!optedOut} disabled aria-label="Improve the Model">
            <Switch.Thumb />
          </Switch.Root>
        )}
      </SetRow>
      <SetRow title="Personalize Grok using X" detail="X personalization stays on grok.com. This sign-in does not report it.">
        <span className="set-value">Not reported</span>
      </SetRow>
      <SetRow title="Personalize with conversation history" detail="Memory across chats stays on grok.com. This sign-in does not report it.">
        <span className="set-value">Not reported</span>
      </SetRow>
      <SetRow title="Delete all conversations" detail="Deletes chats stored on this computer. grok.com history is separate.">
        <button
          type="button"
          className="pill ghost danger"
          onClick={() => {
            if (!confirmDelete) {
              setConfirmDelete(true);
              return;
            }
            void api.deleteAllConversations().then((result) => {
              useDeck.setState({ conversations: [], activeId: null, messages: [] });
              setConfirmDelete(false);
              setNote(result.deleted ? "Conversations on this computer were deleted." : "There were no conversations to delete.");
            }).catch(() => setNote("Could not delete conversations."));
          }}
        >
          {confirmDelete ? "Confirm delete" : "Delete all"}
        </button>
      </SetRow>
      <SetRow title="Download your data" detail="Export desktop chats, branches, and projects. Attached files stay on this computer.">
        <span className="row">
          <button type="button" className="pill ghost" onClick={() => void download("json")}>JSON</button>
          <button type="button" className="pill ghost" onClick={() => void download("md")}>Markdown</button>
        </span>
      </SetRow>
      <SetRow title="Import" detail="Bring back a JSON export of desktop chats and projects.">
        <label className="pill ghost">
          Import JSON
          <input
            type="file"
            accept="application/json"
            hidden
            aria-label="Import JSON"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              void file.text().then((text) => api.importJson(JSON.parse(text))).then(() => {
                void useDeck.getState().load();
                setNote("Import finished.");
              }).catch((error: unknown) => {
                setNote(error instanceof Error ? error.message : "Import failed.");
              });
              event.target.value = "";
            }}
          />
        </label>
      </SetRow>
      <SetRow title="Delete account" detail="Account deletion stays on your xAI account. Nothing is deleted here.">
        <a className="pill ghost" href="https://accounts.x.ai" target="_blank" rel="noreferrer">Open accounts</a>
      </SetRow>
      {note && <p className="note" role="status">{note}</p>}
    </>
  );
}

function UsagePage() {
  const [usage, setUsage] = useState<UsageReport | null>(null);
  const [failed, setFailed] = useState(false);

  function load() {
    setFailed(false);
    setUsage(null);
    void api.usage().then(setUsage).catch(() => setFailed(true));
  }

  useEffect(() => {
    load();
  }, []);

  const kicker = usage?.period === "monthly" ? "MONTHLY LIMIT" : "WEEKLY LIMIT";
  const products = usage?.products || [];
  const used = usage?.usedPercent ?? null;
  const segments = barSegments(products, used);

  return (
    <>
      <Dialog.Title asChild><h2>Usage</h2></Dialog.Title>
      {!usage && !failed && <p className="note">Loading usage…</p>}
      {(failed || usage?.error) && (
        <div className="usage-card">
          <p className="usage-kicker">{kicker}</p>
          <p>{usage?.error || "Couldn't load usage."}</p>
          <button type="button" className="pill ghost" onClick={load}>Retry</button>
        </div>
      )}
      {usage?.ok && (
        <div className="usage-card">
          <p className="usage-kicker">{usage.tier ? `${kicker.replace(" LIMIT", "")} ${usage.tier.toUpperCase()} LIMIT` : kicker}</p>
          <div className="usage-head">
            <div className="usage-pct">
              {used == null ? "—" : formatPercent(used)}
              <span>{used == null ? "not reported" : "used"}</span>
            </div>
            {usage.resetsAt && (
              <div className="usage-reset">
                Resets <b>{formatReset(usage.resetsAt)}</b>
              </div>
            )}
          </div>
          <div className="usage-bar" aria-hidden>
            {segments.map((segment) => (
              <i key={segment.label} style={{ width: `${segment.percent}%`, background: segment.color }} />
            ))}
          </div>
          {products.map((product) => (
            <div key={product.id} className="usage-row">
              <span><i className="usage-dot" style={{ background: product.color }} />{product.label}</span>
              <span>{formatPercent(product.percent)} of total</span>
            </div>
          ))}
          {used != null && products.length === 0 && (
            <div className="usage-row">
              <span><i className="usage-dot" style={{ background: "var(--accent)" }} />Included</span>
              <span>{formatPercent(used)} of total</span>
            </div>
          )}
          <p className="usage-foot">Included usage with your subscription. Resets {usage.period === "monthly" ? "monthly" : "weekly"}. Used before your credits.</p>
        </div>
      )}
    </>
  );
}

function BillingPage({ onUsage }: { onUsage: () => void }) {
  const [usage, setUsage] = useState<UsageReport | null>(null);

  useEffect(() => {
    let live = true;
    void api.usage().then((row) => {
      if (live) setUsage(row);
    }).catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  const cap = usage?.onDemandCap;
  const spent = usage?.onDemandUsed;
  return (
    <>
      <Dialog.Title asChild><h2>Billing</h2></Dialog.Title>
      {!usage && <p className="note">Loading billing…</p>}
      {usage && !usage.ok && <p className="note">{usage.error || "Couldn't load usage."}</p>}
      {usage?.ok && (
        <div className="usage-card">
          <p className="usage-kicker">PLAN</p>
          <div className="usage-pct">{usage.tier || "Grok"}</div>
          <div className="usage-row">
            <span>Included usage</span>
            <span>{usage.usedPercent == null ? "Not reported" : `${formatPercent(usage.usedPercent)} used`}</span>
          </div>
          <div className="usage-row">
            <span>Pay-as-you-go</span>
            <span>{cap && cap > 0 ? `${dollars(spent || 0)} of ${dollars(cap)}` : "Off"}</span>
          </div>
          <div className="usage-row">
            <span>Extra credits</span>
            <span>{dollars(usage.prepaid || 0)}</span>
          </div>
          {usage.resetsAt && (
            <p className="usage-foot">Resets {formatReset(usage.resetsAt)}.</p>
          )}
        </div>
      )}
      <div className="row">
        <button type="button" className="pill ghost" onClick={onUsage}>View usage</button>
        <a className="pill solid" href="https://grok.com?_s=usage" target="_blank" rel="noreferrer">Manage subscription</a>
      </div>
    </>
  );
}

function SetRow({ title, detail, children }: { title: string; detail?: string; children?: ReactNode }) {
  return (
    <div className="set-row">
      <div className="set-copy">
        <h3>{title}</h3>
        {detail && <p>{detail}</p>}
      </div>
      {children}
    </div>
  );
}

function Toggle({
  title,
  detail,
  checked,
  onChange,
}: {
  title: string;
  detail: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <SetRow title={title} detail={detail}>
      <Switch.Root className="switch" checked={checked} onCheckedChange={onChange} aria-label={title}>
        <Switch.Thumb />
      </Switch.Root>
    </SetRow>
  );
}

function styleId(text: string) {
  const match = STYLES.find((item) => item.text != null && item.text === text);
  return match?.id || "custom";
}

function formatPercent(value: number) {
  const rounded = Math.round(value * 10) / 10;
  return `${Number.isInteger(rounded) ? rounded.toFixed(0) : rounded}%`;
}

function formatReset(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const weekday = date.toLocaleDateString(undefined, { weekday: "short" });
  const month = date.toLocaleDateString(undefined, { month: "short" });
  const time = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return `${weekday} ${date.getDate()} ${month} at ${time}`;
}

function dollars(value: number) {
  return value.toLocaleString(undefined, { style: "currency", currency: "USD" });
}

function barSegments(products: UsageReport["products"], used: number | null) {
  if (products.length) return products;
  if (used == null || used <= 0) return [];
  return [{ label: "Included", percent: used, color: "var(--accent)" }];
}

async function download(fmt: "json" | "md") {
  const data = fmt === "md" ? await api.exportMarkdown() : await api.exportJson();
  const blob = new Blob([typeof data === "string" ? data : JSON.stringify(data, null, 2)], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fmt === "md" ? "grokdeck.md" : "grokdeck.json";
  link.click();
  URL.revokeObjectURL(url);
}
