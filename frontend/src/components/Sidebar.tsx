import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as ScrollArea from "@radix-ui/react-scroll-area";
import { Bot, Image as ImageIcon, Library, Lock, LogOut, MessageCircle, MoreHorizontal, PanelLeft, PenLine, Pin, Plus, Search, Settings, SquarePen, Terminal, Trash2, UserRound, Workflow } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { BuildSessionPane } from "./BuildView";
import { api } from "../lib/api";
import { useDeck } from "../state";
import type { Conversation, ProviderModel } from "../types";

export function Sidebar() {
  const navigate = useNavigate();
  const conversations = useDeck((s) => s.conversations);
  const hiddenIds = useDeck((s) => s.hiddenIds);
  const projects = useDeck((s) => s.projects);
  const activeId = useDeck((s) => s.activeId);
  const openConversation = useDeck((s) => s.openConversation);
  const newChat = useDeck((s) => s.newChat);
  const setSearchOpen = useDeck((s) => s.setSearchOpen);
  const toggleSidebar = useDeck((s) => s.toggleSidebar);
  const setSettingsOpen = useDeck((s) => s.setSettingsOpen);
  const settingsOpen = useDeck((s) => s.settingsOpen);
  const settings = useDeck((s) => s.settings);
  const privateOn = useDeck((s) => s.privateOn);
  const grokBot = useDeck((s) => s.grokBot);
  const setPrivate = useDeck((s) => s.setPrivate);
  const account = useDeck((s) => s.account);
  const signIn = useDeck((s) => s.signIn);
  const signOut = useDeck((s) => s.signOut);
  const cancelSignIn = useDeck((s) => s.cancelSignIn);
  const rename = useDeck((s) => s.rename);
  const pin = useDeck((s) => s.pin);
  const remove = useDeck((s) => s.remove);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [models, setModels] = useState<ProviderModel[]>([]);
  const [modelNote, setModelNote] = useState("");
  const [accountNote, setAccountNote] = useState("");
  const [webState, setWebState] = useState("loading");
  const path = useLocation().pathname;
  const onBuild = path === "/build";
  const selectedModel = activeId && path === "/local"
    ? conversations.find((item) => item.id === activeId)?.model || settings?.activeModel
    : settings?.activeModel;

  useEffect(() => {
    if (settingsOpen) return;
    void api.providers().then((data) => setModels(data.models)).catch(() => setModelNote("Models did not load."));
  }, [settingsOpen]);

  useEffect(() => {
    const update = (event: Event) => setWebState((event as CustomEvent<{ status?: string }>).detail?.status || "loading");
    window.addEventListener("grokdeck:web-state", update);
    return () => window.removeEventListener("grokdeck:web-state", update);
  }, []);

  async function chooseModel(value: string) {
    setModelNote("");
    if (value === "web") {
      navigate("/");
      return;
    }
    try {
      await useDeck.getState().saveSettings({ activeModel: value });
      if (path === "/local" && activeId) {
        await api.patchConversation(activeId, { model: value });
        useDeck.setState((state) => ({ conversations: state.conversations.map((item) => item.id === activeId ? { ...item, model: value } : item) }));
      } else {
        newChat();
      }
      navigate("/local");
    } catch (error) {
      setModelNote(error instanceof Error ? error.message : "Could not select the model.");
    }
  }

  const visible = conversations.filter((item) => !hiddenIds.includes(item.id));
  const pinned = visible.filter((item) => item.pinned);
  const rest = visible.filter((item) => !item.pinned);
  const shown = expanded ? rest : rest.slice(0, 6);

  function openChat(id: string) {
    void openConversation(id);
    navigate("/local");
  }

  async function addProject() {
    const name = projectName.trim();
    if (!name) return;
    const created = await api.createProject(name, "");
    useDeck.setState((state) => ({ projects: [...state.projects, created] }));
    setProjectName("");
    setAdding(false);
  }

  function renderRow(item: Conversation) {
    const isEditing = editing === item.id;
    return (
      <div key={item.id} className={item.id === activeId ? "chat-row active" : "chat-row"}>
        {isEditing ? (
          <input
            aria-label="Edit name"
            value={draft}
            autoFocus
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                void rename(item.id, draft.trim() || item.title);
                setEditing(null);
              }
              if (event.key === "Escape") setEditing(null);
            }}
            onBlur={() => setEditing(null)}
          />
        ) : (
          <button type="button" onClick={() => openChat(item.id)}>
            <span>{item.title || "New chat"}</span>
          </button>
        )}
        <div className="row-actions">
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button type="button" aria-label={`Actions for ${item.title}`}>
                <MoreHorizontal size={16} />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content className="menu" side="right" align="start" sideOffset={6}>
                <DropdownMenu.Item className="menu-item" onSelect={() => { setDraft(item.title); setEditing(item.id); }}>
                  <PenLine size={14} /> Rename
                </DropdownMenu.Item>
                <DropdownMenu.Item className="menu-item" onSelect={() => void pin(item.id)}>
                  <Pin size={14} /> {item.pinned ? "Unpin" : "Pin"}
                </DropdownMenu.Item>
                <DropdownMenu.Item className="menu-item" onSelect={() => { if (window.confirm("Delete this chat?")) void remove(item.id); }}>
                  <Trash2 size={14} /> Delete
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </div>
    );
  }

  return (
    <aside className="sidebar" aria-label="Chats">
      <div className="sidebar-tools">
        <button type="button" className="icon-btn" aria-label="Close sidebar" onClick={toggleSidebar}>
          <PanelLeft size={18} />
        </button>
        <button type="button" className="icon-btn" aria-label="Search" onClick={() => setSearchOpen(true)}>
          <Search size={18} />
        </button>
        <button
          type="button"
          className="icon-btn"
          aria-label="New chat"
          onClick={() => {
            if (path === "/" && window.grokdeck?.grokWeb) {
              void window.grokdeck.grokWeb.newChat();
              return;
            }
            newChat();
            navigate("/local");
          }}
        >
          <SquarePen size={18} />
        </button>
      </div>
      <nav className="side-nav" aria-label="Primary">
        <NavLink to="/" end className={({ isActive }) => `nav-item${isActive || path === "/local" ? " active" : ""}`}>
          <MessageCircle size={16} /> Grok Chat
        </NavLink>
        <NavLink to="/build" className="nav-item">
          <Terminal size={16} /> Build
        </NavLink>
        <NavLink to="/imagine" className="nav-item">
          <ImageIcon size={16} /> Imagine
        </NavLink>
        <NavLink to="/library" className="nav-item">
          <Library size={16} /> Library
        </NavLink>
        <NavLink to="/automations" className="nav-item">
          <Workflow size={16} /> Automations
        </NavLink>
        <button type="button" className="nav-item" disabled={!grokBot || !window.grokdeck?.openGrokBot} title={!grokBot ? "Grok Bot is not installed" : undefined} onClick={() => void window.grokdeck?.openGrokBot?.()}>
          <Bot size={16} /> Grok Bot
        </button>
        {path === "/local" && <button type="button" className="nav-item" aria-pressed={privateOn} onClick={() => setPrivate(!privateOn)}>
          <Lock size={16} /> Private chat
        </button>}
      </nav>
      {!onBuild && <div className="sidebar-model">
        <label htmlFor="chat-model">Chat model</label>
        <select id="chat-model" aria-label="Chat model" value={path === "/" ? "web" : selectedModel || "grok:subscription"} onChange={(event) => void chooseModel(event.target.value)}>
          <option value="web">Grok web</option>
          {models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
        </select>
        {modelNote && <span role="status" className="note">{modelNote}</span>}
      </div>}
      <ScrollArea.Root className="sidebar-list">
        <ScrollArea.Viewport style={{ height: "100%" }}>
          {onBuild ? <BuildSessionPane /> : <>
          <div className="group-label">Projects</div>
          {adding ? (
            <form
              className="project-add"
              onSubmit={(event) => {
                event.preventDefault();
                void addProject();
              }}
            >
              <input
                aria-label="Project name"
                value={projectName}
                autoFocus
                placeholder="Project name"
                onChange={(event) => setProjectName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setAdding(false);
                }}
              />
            </form>
          ) : (
            <button type="button" className="side-btn subtle" onClick={() => setAdding(true)}>
              <Plus size={16} /> Add project
            </button>
          )}
          {projects.map((project) => (
            <div key={project.id} className="chat-row">
              <span>{project.name}</span>
            </div>
          ))}
          <div className="group-label">Chats</div>
          {pinned.map(renderRow)}
          {shown.map(renderRow)}
          {rest.length > 6 && (
            <button type="button" className="side-btn subtle" onClick={() => setExpanded((value) => !value)}>
              {expanded ? "Show less" : "See all"}
            </button>
          )}
          </>}
        </ScrollArea.Viewport>
        <ScrollArea.Scrollbar orientation="vertical">
          <ScrollArea.Thumb />
        </ScrollArea.Scrollbar>
      </ScrollArea.Root>
      <div className="sidebar-foot">
        <AccountMenu
          signedIn={!!account?.signedIn}
          waiting={account?.state === "waiting"}
          failed={account?.message || ""}
          onSettings={() => setSettingsOpen(true)}
          onSignIn={() => void signIn()}
          onSignOut={() => void signOut()}
          onSwitchCli={() => void signOut().then(signIn).catch((error: unknown) => setAccountNote(error instanceof Error ? error.message : "Could not switch the Build account."))}
          onSwitchWeb={() => {
            const bridge = window.grokdeck?.grokWeb;
            if (!bridge) return;
            void bridge.signOut().then(() => {
              navigate("/");
              window.dispatchEvent(new Event("grokdeck:web-reset"));
            }).catch((error: unknown) => setAccountNote(error instanceof Error ? error.message : "Could not switch the web account."));
          }}
          onCancel={() => void cancelSignIn()}
          note={accountNote}
          webState={webState}
        />
      </div>
    </aside>
  );
}

function AccountMenu({
  signedIn,
  waiting,
  failed,
  onSettings,
  onSignIn,
  onSignOut,
  onSwitchCli,
  onSwitchWeb,
  onCancel,
  note,
  webState,
}: {
  signedIn: boolean;
  waiting: boolean;
  failed: string;
  onSettings: () => void;
  onSignIn: () => void;
  onSignOut: () => void;
  onSwitchCli: () => void;
  onSwitchWeb: () => void;
  onCancel: () => void;
  note: string;
  webState: string;
}) {
  const label = waiting ? "Build CLI: opening browser…" : signedIn ? "Build CLI: signed in" : "Sign in to Build CLI";
  const webNote = webState === "signing-in"
    ? "Grok Chat: finish sign-in in the chat window."
    : webState === "error"
      ? "Grok Chat: reload the chat window to retry."
      : "Grok Chat has its own browser session. Switching clears this app's saved chat session only.";
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button type="button" className="account" aria-label="Account">
          <span className={waiting ? "avatar wait" : "avatar"} aria-hidden="true">
            {signedIn ? "B" : <UserRound size={14} />}
          </span>
          <span className="account-name">{label}</span>
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="menu account-menu" side="top" align="start" sideOffset={8}>
          {waiting && <div className="menu-note">Finish sign-in in the browser window.</div>}
          {!waiting && failed && <div className="menu-note">{failed}</div>}
          {note && <div className="menu-note" role="status">{note}</div>}
          <div className="menu-note">{webNote}</div>
          <DropdownMenu.Item className="menu-item" onSelect={onSettings}>
            <Settings size={16} /> Settings
          </DropdownMenu.Item>
          {waiting ? (
            <DropdownMenu.Item className="menu-item" onSelect={onCancel}>
              Cancel
            </DropdownMenu.Item>
          ) : signedIn ? (
            <>
              <DropdownMenu.Item className="menu-item" onSelect={onSwitchCli}>Switch Build account</DropdownMenu.Item>
              <DropdownMenu.Item className="menu-item" onSelect={onSignOut}>
                <LogOut size={16} /> Sign out of Build
              </DropdownMenu.Item>
            </>
          ) : (
            <DropdownMenu.Item className="menu-item" onSelect={onSignIn}>
              Sign in to Grok CLI
            </DropdownMenu.Item>
          )}
          {window.grokdeck?.grokWeb && <DropdownMenu.Item className="menu-item" onSelect={onSwitchWeb}>Switch Grok Chat account</DropdownMenu.Item>}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
