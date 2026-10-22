import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowUp, BrainCircuit, ChevronDown, FolderOpen, GitBranch, Mic, Plus, Puzzle, Square, WandSparkles } from "lucide-react";
import { useVoiceCapture } from "../lib/voice";
import { memo, useCallback, useEffect, useRef, useState } from "react";
import { beginBuildOperation, isBuildOperation, loadBuildFiles, openBuildSession, refreshBuildSessions, useBuild } from "../lib/buildState";
import { api, streamRequest } from "../lib/api";
import { useDeck, workerContext } from "../state";
import type { CliSnapshot, CliTool, CliTurn, Project, ServerEvent } from "../types";
import { MarkButton } from "./TopActions";

const EASE = [0.22, 1, 0.36, 1] as const;
const BUILD_MODELS = ["grok-4.7", "grok-4.7-build-fast", "grok-4.6", "grok-4.5"];
const EFFORTS = ["high", "xhigh", "medium", "low"] as const;
const BUILD_MODES = [
  ["ask", "Ask"],
  ["plan", "Plan"],
  ["accept-edits", "Accept edits"],
  ["full-auto", "Full auto"],
] as const;

function basename(path: string) {
  const trimmed = path.replace(/[\\/]+$/, "");
  return trimmed.split(/[\\/]/).pop() || trimmed;
}

function errorText(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function emptyTurn(id: string, role: string, text = ""): CliTurn {
  return { id, role, text, reasoning: "", tools: [], verbose: "" };
}

function commandName(command: string | { name: string }) {
  const name = typeof command === "string" ? command : command.name;
  return name.startsWith("/") ? name : `/${name}`;
}

function toolLine(tool: CliTool) {
  return [tool.name, tool.path, tool.status].filter(Boolean).join("  ");
}

/* Verbose is the default and only view: show the CLI's verbose string, else the text plus tool lines. */
const TurnBlock = memo(function TurnBlock({ turn }: { turn: CliTurn }) {
  const verbose = turn.verbose?.trim() ? turn.verbose : "";
  return (
    <div className={`build-turn ${turn.role === "user" ? "user" : "agent"}`}>
      {verbose ? (
        <pre>{verbose}</pre>
      ) : (
        <>
          {turn.text && <pre>{turn.text}</pre>}
          {(turn.tools || []).map((tool) => (
            <pre key={tool.id} className="build-tool">
              {toolLine(tool)}
            </pre>
          ))}
        </>
      )}
    </div>
  );
});

export function BuildView() {
  const settings = useDeck((s) => s.settings);
  const saveSettings = useDeck((s) => s.saveSettings);
  const sidebarOpen = useDeck((s) => s.sidebarOpen);
  const reduceMotion = useReducedMotion();
  const sessions = useBuild((s) => s.sessions);
  const signedIn = useBuild((s) => s.signedIn);
  const folder = useBuild((s) => s.folder);
  const instructions = useBuild((s) => s.instructions);
  const snapshot = useBuild((s) => s.snapshot);
  const turns = useBuild((s) => s.turns);
  const files = useBuild((s) => s.files);
  const note = useBuild((s) => s.note);
  const handoffPrompt = useBuild((s) => s.handoffPrompt);
  const setSnapshot = useBuild((s) => s.setSnapshot);
  const setTurns = useBuild((s) => s.setTurns);
  const setNote = useBuild((s) => s.setNote);

  const [pending, setPending] = useState<{ id: string; detail: string } | null>(null);
  const running = useBuild((s) => Boolean(s.operation));
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState(snapshot?.model || BUILD_MODELS[0]);
  const [effort, setEffort] = useState(snapshot?.effort || "high");
  const [buildMode, setBuildMode] = useState<(typeof BUILD_MODES)[number][0]>("ask");
  const [gitOpen, setGitOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState<"plugins" | "skills" | null>(null);
  const [catalog, setCatalog] = useState<{ plugins: string[]; skills: string[] }>({ plugins: [], skills: [] });
  const voice = useVoiceCapture((heard) => {
    setPrompt((current) => (current.trim() ? `${current.trim()} ${heard}` : heard));
  });

  const abortRef = useRef<AbortController | null>(null);
  const liveRef = useRef<string | null>(null);
  const termRef = useRef<HTMLDivElement | null>(null);
  const followRef = useRef(true);
  const selectionRef = useRef("");

  const cwd = folder || snapshot?.session.cwd || settings?.defaultCwd || "";
  const signedOut = signedIn === false && sessions.length === 0;
  const buildOff = settings?.buildCli === false;
  const sessionId = snapshot?.session.id || null;
  const availableModels = snapshot?.models?.length
    ? snapshot.models.map((item) => typeof item === "string" ? { id: item, efforts: [] as readonly string[] } : {
      id: item.id,
      efforts: item._meta?.reasoningEfforts || item._meta?.reasoning_efforts || [] as readonly string[],
    })
    : BUILD_MODELS.map((id) => ({ id, efforts: EFFORTS as readonly string[] }));
  const selectedModel = availableModels.find((item) => item.id === model) || availableModels[0];
  const availableEfforts = selectedModel?.efforts.length ? selectedModel.efforts : snapshot?.efforts?.length ? snapshot.efforts : EFFORTS;

  const refreshSessions = useCallback(async () => {
    await refreshBuildSessions();
  }, []);

  const loadFiles = useCallback(async (path: string) => {
    await loadBuildFiles(path);
  }, []);

  // Mount only lists sessions. No prompt, no session open, no file scan.
  useEffect(() => {
    void refreshSessions();
    return () => abortRef.current?.abort();
  }, [refreshSessions]);

  useEffect(() => {
    if (!handoffPrompt) return;
    setPrompt(handoffPrompt);
    useBuild.setState({ handoffPrompt: "" });
  }, [handoffPrompt]);

  useEffect(() => {
    followRef.current = true;
  }, [sessionId]);

  useEffect(() => {
    const node = termRef.current;
    if (node && followRef.current) node.scrollTop = node.scrollHeight;
  }, [turns, pending]);

  useEffect(() => {
    const key = JSON.stringify([sessionId, snapshot?.model, snapshot?.effort]);
    const restore = selectionRef.current !== key;
    selectionRef.current = key;
    const requestedModel = restore ? snapshot?.model || model : model;
    const chosen = availableModels.find((item) => item.id === requestedModel) || availableModels[0];
    const nextModel = chosen?.id || requestedModel;
    const efforts = chosen?.efforts.length ? chosen.efforts : snapshot?.efforts?.length ? snapshot.efforts : EFFORTS;
    const requestedEffort = restore ? snapshot?.effort || effort : effort;
    const nextEffort = efforts.includes(requestedEffort) ? requestedEffort : efforts[0];
    if (nextModel !== model) setModel(nextModel);
    if (nextEffort && nextEffort !== effort) setEffort(nextEffort);
  }, [availableModels, effort, model, sessionId, snapshot?.model, snapshot?.effort, snapshot?.efforts]);

  function applySnapshot(snap: CliSnapshot) {
    setSnapshot(snap);
    setTurns(snap.messages || []);
    if (snap.model) setModel(snap.model);
    if (snap.effort) setEffort(snap.effort);
    liveRef.current = null;
  }

  function patchLive(update: (turn: CliTurn) => CliTurn) {
    setTurns((current) => {
      const id = liveRef.current;
      const index = id ? current.findIndex((turn) => turn.id === id) : -1;
      if (index === -1) {
        const fresh = emptyTurn(`live-${Date.now()}`, "assistant");
        liveRef.current = fresh.id;
        return [...current, update(fresh)];
      }
      const next = current.slice();
      next[index] = update(current[index]);
      return next;
    });
  }

  function onEvent(event: ServerEvent) {
    switch (event.event) {
      case "snapshot":
        if (event.snapshot) applySnapshot(event.snapshot);
        return;
      case "turn":
      case "message": {
        const turn = event.turn;
        if (!turn) return;
        setTurns((current) => {
          const existing = current.findIndex((item) => item.id === turn.id);
          if (existing !== -1) {
            const next = current.slice();
            next[existing] = turn;
            return next;
          }
          const live = liveRef.current ? current.findIndex((item) => item.id === liveRef.current) : -1;
          if (live !== -1 && turn.role !== "user") {
            const next = current.slice();
            next[live] = turn;
            liveRef.current = turn.id;
            return next;
          }
          return [...current, turn];
        });
        return;
      }
      case "tool": {
        setNote("");
        const tool = event.tool;
        if (!tool) return;
        patchLive((turn) => {
          const tools = turn.tools.some((item) => item.id === tool.id)
            ? turn.tools.map((item) => (item.id === tool.id ? tool : item))
            : [...turn.tools, tool];
          return { ...turn, tools };
        });
        return;
      }
      case "permission":
        setNote("");
        setPending({ id: String(event.id || ""), detail: event.detail || event.text || event.message || "" });
        return;
      case "command":
        if (event.commands) useBuild.setState((state) => ({
          snapshot: state.snapshot ? { ...state.snapshot, commands: event.commands! } : null,
        }));
        return;
      case "error":
        setNote(event.message || event.text || "Grok Build stopped with an error.");
        return;
      case "done":
        setNote("");
        return;
      default: {
        const verbose = event.verbose;
        const text = event.text;
        if (verbose === undefined && text === undefined) return;
        setNote("");
        patchLive((turn) => ({
          ...turn,
          text: text !== undefined && event.event !== "verbose" ? turn.text + text : turn.text,
          verbose: turn.verbose + (verbose ?? text ?? ""),
        }));
      }
    }
  }

  async function submit() {
    const text = prompt.trim();
    if (!text || running || signedOut || buildOff) return;
    if (!cwd) {
      setNote("Pick a folder first.");
      return;
    }
    const controller = beginBuildOperation();
    if (!controller) return;
    setNote("");
    setPrompt("");
    let id = sessionId;
    abortRef.current = controller;
    followRef.current = true;
    setNote("Waiting for Grok Build…");
    let startedTurn = false;
    let turnModel = model;
    let turnEffort = effort;
    try {
      const reports = await workerContext(text, settings, controller.signal);
      if (!isBuildOperation(controller)) return;
      if (!id) {
        const created = await api.buildOpen(cwd, instructions, buildMode);
        if (!isBuildOperation(controller)) return;
        if (created.models?.length && !created.models.some((item) => item.id === turnModel)) turnModel = created.model || created.models[0].id;
        if (created.efforts?.length && !created.efforts.includes(turnEffort)) turnEffort = created.effort || created.efforts[0];
        applySnapshot({ session: created, messages: [], models: created.models || [], model: turnModel,
          effort: turnEffort, efforts: created.efforts, commands: created.commands || [] });
        setModel(turnModel);
        setEffort(turnEffort);
        id = created.id;
        void refreshSessions();
      }
      liveRef.current = null;
      setTurns((current) => [...current, emptyTurn(`local-${Date.now()}`, "user", text)]);
      startedTurn = true;
      await streamRequest(
        `/api/build/sessions/${encodeURIComponent(id)}/prompt`,
        { text, cwd, model: turnModel, effort: turnEffort, mode: buildMode, ...(reports ? { workerContext: reports } : {}) },
        (event) => { if (isBuildOperation(controller)) onEvent(event); },
        controller.signal,
      );
    } catch (error) {
      if (!isBuildOperation(controller)) return;
      if (!startedTurn) setPrompt(text);
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setNote(errorText(error, "Grok Build did not start."));
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      if (useBuild.getState().operation === controller) {
        useBuild.setState({ operation: null });
        liveRef.current = null;
        setPending(null);
        if (controller.signal.aborted) setNote("");
        void loadFiles(cwd);
        void refreshSessions();
      }
    }
  }

  async function stop() {
    const operation = useBuild.getState().operation;
    operation?.abort();
    if (!sessionId) {
      return;
    }
    try {
      await api.buildStop(sessionId);
    } catch (error) {
      if (useBuild.getState().operation === operation) setNote(errorText(error, "Stop did not reach the CLI."));
    }
  }

  async function answer(allow: boolean) {
    if (!pending || !sessionId) return;
    const request = pending;
    setPending(null);
    try {
      await api.buildPermission(sessionId, request.id, allow);
    } catch (error) {
      setNote(errorText(error, "The answer did not reach the CLI."));
    }
  }

  const commands = prompt.startsWith("/")
    ? (snapshot?.commands || []).filter((command) => commandName(command).toLowerCase().startsWith(prompt.trim().toLowerCase())).slice(0, 8)
    : [];

  const active = Boolean(sessionId) || turns.length > 0;

  return (
    <div className={active ? "thread-screen build-stage" : "stage home build-stage"}>
      {!window.grokdeck && (
        <div className="top">
          <div>{!sidebarOpen && <MarkButton />}</div>
        </div>
      )}

      {!active && <h1 className="home-title">What should we build?</h1>}

      {active && (
      <section className="build-main" aria-label="Grok Build">
        <div className="build-head">
          <span className="build-title">{snapshot?.session.title || "New session"}</span>
          <span className="build-badge">Verbose</span>
        </div>

        <motion.div
          key={sessionId || "new"}
          className="build-verbose"
          ref={termRef}
          onScroll={(event) => {
            const node = event.currentTarget;
            followRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
          }}
          role="log"
          aria-live="polite"
          aria-label="Verbose terminal"
          initial={reduceMotion ? false : { opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.22, ease: EASE }}
        >
          {signedOut ? (
            <p className="build-quiet">Sign in from the sidebar to use Grok Build.</p>
          ) : turns.length === 0 ? (
            <p className="build-quiet">{cwd ? `Ready in ${basename(cwd)}.` : "Pick a folder to start."}</p>
          ) : (
            turns.map((turn) => <TurnBlock key={turn.id} turn={turn} />)
          )}
          {running && <span className="build-caret" aria-hidden="true" />}
          {pending && (
            <div className="build-permission" role="group" aria-label="Permission request">
              <pre>{pending.detail || "Grok wants to run a tool."}</pre>
              <div className="build-permission-actions">
                <button type="button" className="pill solid" onClick={() => void answer(true)}>
                  Allow
                </button>
                <button type="button" className="pill ghost" onClick={() => void answer(false)}>
                  Deny
                </button>
              </div>
            </div>
          )}
        </motion.div>

        {commands.length > 0 && (
          <div className="build-commands" aria-label="Slash commands">
            {commands.map((command) => {
              const name = commandName(command);
              return (
                <button
                  key={name}
                  type="button"
                  className="chip quiet"
                  title={typeof command === "string" ? undefined : command.description}
                  onClick={() => setPrompt(`${name} `)}
                >
                  {name}
                </button>
              );
            })}
          </div>
        )}
      </section>
      )}

      <div className={active ? "thread-composer" : "composer-wrap"}>
        {gitOpen && (
          <div className="build-git" aria-label="Git">
            <div className="build-git-head">
              <GitBranch size={14} aria-hidden="true" />
              <span>{files?.branch || "No branch"}</span>
              <span className="note">{(files?.files || []).length} changed</span>
            </div>
            {(files?.files || []).map((file) => (
              <div key={file.path} className="file-row">
                <span>{file.path}</span>
                <span>{file.status}</span>
              </div>
            ))}
            {(files?.files || []).length === 0 && <p className="note">No git changes</p>}
            {files?.diff && (
              <div className="diff build-diff">
                {files.diff.split("\n").map((line, index) => (
                  <div key={`${index}-${line.slice(0, 8)}`} className={line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : undefined}>
                    {line}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {toolsOpen && (
          <div className="build-git" role="dialog" aria-label={toolsOpen === "plugins" ? "Plugins" : "Skills"}>
            <div className="build-git-head">
              <span>{toolsOpen === "plugins" ? "Plugins" : "Skills"}</span>
              <button type="button" className="pill ghost" onClick={() => setToolsOpen(null)}>Close</button>
            </div>
            {(toolsOpen === "plugins" ? catalog.plugins : catalog.skills).map((name) => (
              <button
                key={name}
                type="button"
                className="menu-item"
                onClick={() => {
                  setPrompt((value) => (value.trim() ? value : `/${name} `));
                  setToolsOpen(null);
                }}
              >
                {name}
              </button>
            ))}
            {(toolsOpen === "plugins" ? catalog.plugins : catalog.skills).length === 0 && (
              <p className="note">None installed in the grok CLI yet.</p>
            )}
          </div>
        )}
        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <textarea
            aria-label="Build prompt"
            rows={2}
            placeholder={signedOut ? "Sign in to use Grok Build" : buildOff ? "Enable Build to start" : "Type / to use slash commands"}
            value={prompt}
            disabled={signedOut || buildOff}
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void submit();
              }
            }}
          />
          <div className="composer-bar">
            <button
              type="button"
              className={gitOpen ? "model-btn on" : "model-btn"}
              aria-pressed={gitOpen}
              aria-label="Git and diff"
              onClick={() => {
                setGitOpen((open) => !open);
                if (cwd) void loadFiles(cwd);
              }}
            >
              <GitBranch size={14} /> Git
            </button>
            <button
              type="button"
              className="model-btn"
              aria-label="Plugins"
              onClick={() => {
                setToolsOpen("plugins");
                void api.buildCatalog().then(setCatalog).catch(() => setCatalog({ plugins: [], skills: [] }));
              }}
            >
              <Puzzle size={14} /> Plugins
            </button>
            <button
              type="button"
              className="model-btn"
              aria-label="Skills"
              onClick={() => {
                setToolsOpen("skills");
                void api.buildCatalog().then(setCatalog).catch(() => setCatalog({ plugins: [], skills: [] }));
              }}
            >
              <WandSparkles size={14} /> Skills
            </button>
            <span style={{ flex: 1 }} />
            <DropdownMenu.Root>
              <DropdownMenu.Trigger className="model-btn" aria-label={`Build mode: ${BUILD_MODES.find(([id]) => id === buildMode)?.[1] || "Ask"}`}>
                {BUILD_MODES.find(([id]) => id === buildMode)?.[1] || "Ask"}
                <ChevronDown size={14} />
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content className="menu model-menu" side="top" align="end" sideOffset={8}>
                  <DropdownMenu.RadioGroup value={buildMode} onValueChange={(value) => setBuildMode(value as typeof buildMode)}>
                    {BUILD_MODES.map(([id, label]) => (
                      <DropdownMenu.RadioItem key={id} className="menu-item" value={id}>
                        <span>{label}</span>
                        <DropdownMenu.ItemIndicator className="menu-check">✓</DropdownMenu.ItemIndicator>
                      </DropdownMenu.RadioItem>
                    ))}
                  </DropdownMenu.RadioGroup>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
            <DropdownMenu.Root>
              <DropdownMenu.Trigger className="model-btn" aria-label={`Model: ${model}`}>
                <span className="model-btn-label">{model}</span>
                <ChevronDown size={14} />
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content className="menu model-menu" side="top" align="end" sideOffset={8}>
                  <DropdownMenu.RadioGroup value={model} onValueChange={setModel}>
                    {availableModels.map(({ id }) => (
                      <DropdownMenu.RadioItem key={id} className="menu-item" value={id}>
                        <span>{id}</span>
                        <DropdownMenu.ItemIndicator className="menu-check">✓</DropdownMenu.ItemIndicator>
                      </DropdownMenu.RadioItem>
                    ))}
                  </DropdownMenu.RadioGroup>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
            <DropdownMenu.Root>
              <DropdownMenu.Trigger className="model-btn" aria-label={`Thinking: ${effort}`}>
                <BrainCircuit size={14} />
                <span className="model-btn-label">{effort}</span>
                <ChevronDown size={14} />
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content className="menu model-menu" side="top" align="end" sideOffset={8}>
                  <div className="slash-label">Thinking</div>
                  <DropdownMenu.RadioGroup value={effort} onValueChange={setEffort}>
                    {availableEfforts.map((id) => (
                      <DropdownMenu.RadioItem key={id} className="menu-item" value={id}>
                        <span>{id}</span>
                        <DropdownMenu.ItemIndicator className="menu-check">✓</DropdownMenu.ItemIndicator>
                      </DropdownMenu.RadioItem>
                    ))}
                  </DropdownMenu.RadioGroup>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
            {prompt.trim() || running ? (
              <button
                type={running ? "button" : "submit"}
                className="send"
                aria-label={running ? "Stop" : "Submit"}
                onClick={running ? () => void stop() : undefined}
              >
                {running ? <Square size={14} fill="currentColor" /> : <ArrowUp size={18} />}
              </button>
            ) : (
              <button
                type="button"
                className="icon-btn"
                aria-label="Microphone"
                aria-pressed={voice.listening}
                onClick={() => void voice.toggle()}
              >
                <Mic size={16} />
              </button>
            )}
          </div>
        </form>
        {buildOff && !signedOut && (
          <p className="note build-note" role="status">
            Build is off in Settings. <button type="button" className="pill ghost" onClick={() => void saveSettings({ buildCli: true })}>Enable Build</button>
          </p>
        )}
        {(voice.listening || voice.notice) && (
          <p className="banner" role="status">{voice.listening ? "Listening. Tap again to finish." : voice.notice}</p>
        )}
        {note && (
          <p className="note build-note" role="status" data-testid="build-note">
            {note}
          </p>
        )}
      </div>

    </div>
  );
}

export function BuildSessionPane() {
  const projects = useDeck((s) => s.projects);
  const sessions = useBuild((s) => s.sessions);
  const signedIn = useBuild((s) => s.signedIn);
  const projectId = useBuild((s) => s.projectId);
  const snapshot = useBuild((s) => s.snapshot);
  const setFolder = useBuild((s) => s.setFolder);
  const setInstructions = useBuild((s) => s.setInstructions);
  const setProjectId = useBuild((s) => s.setProjectId);
  const resetStored = useBuild((s) => s.resetSession);
  const busy = useBuild((s) => Boolean(s.operation));
  const sessionId = snapshot?.session.id || null;
  const signedOut = signedIn === false && sessions.length === 0;

  useEffect(() => {
    void refreshBuildSessions();
  }, []);

  async function pickFolder() {
    if (useBuild.getState().operation || !window.grokdeck?.pickDirectory) return;
    const path = await window.grokdeck.pickDirectory();
    if (!path || useBuild.getState().operation) return;
    setFolder(path);
    void loadBuildFiles(path);
  }

  function chooseProject(project: Project) {
    if (useBuild.getState().operation) return;
    setProjectId(project.id);
    setInstructions(project.instructions || "");
    if (project.path) {
      setFolder(project.path);
      void loadBuildFiles(project.path);
    }
  }

  return (
    <>
      <button type="button" className="side-btn" disabled={busy} onClick={() => resetStored()}>
        <Plus size={16} aria-hidden="true" /> New session
      </button>
      <button type="button" className="side-btn" disabled={busy} onClick={() => void pickFolder()}>
        <FolderOpen size={16} aria-hidden="true" /> Folder
      </button>
      {projects.length > 0 && (
        <>
          <div className="group-label">Projects</div>
          {projects.map((project) => (
            <button
              key={project.id}
              type="button"
              className={projectId === project.id ? "chat-row active" : "chat-row"}
              disabled={busy}
              onClick={() => chooseProject(project)}
            >
              <span>{project.name}</span>
            </button>
          ))}
        </>
      )}
      <div className="group-label">Sessions</div>
      {sessions.map((session) => (
        <button
          key={session.id}
          type="button"
          className={sessionId === session.id ? "chat-row active" : "chat-row"}
          disabled={busy}
          onClick={() => void openBuildSession(session)}
        >
          <span>{session.title || "Untitled session"}</span>
        </button>
      ))}
      {signedIn !== null && sessions.length === 0 && !signedOut && <p className="note">No sessions yet</p>}
      {signedOut && <p className="note">Sign in from the account menu.</p>}
    </>
  );
}
