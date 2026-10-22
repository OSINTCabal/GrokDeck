import { create } from "zustand";
import { api } from "./api.ts";
import type { CliFiles, CliSession, CliSnapshot, CliTurn } from "../types";

interface BuildState {
  operation: AbortController | null;
  sessions: CliSession[];
  signedIn: boolean | null;
  folder: string;
  instructions: string;
  projectId: string | null;
  snapshot: CliSnapshot | null;
  turns: CliTurn[];
  files: CliFiles | null;
  note: string;
  handoffPrompt: string;
  setSessions: (sessions: CliSession[], signedIn: boolean) => void;
  setFolder: (folder: string) => void;
  setInstructions: (instructions: string) => void;
  setProjectId: (projectId: string | null) => void;
  setSnapshot: (snapshot: CliSnapshot | null) => void;
  setTurns: (turns: CliTurn[] | ((current: CliTurn[]) => CliTurn[])) => void;
  setFiles: (files: CliFiles | null) => void;
  setNote: (note: string) => void;
  resetSession: () => void;
}

let sessionGeneration = 0;
let filesGeneration = 0;
let listGeneration = 0;

export const useBuild = create<BuildState>((set, get) => ({
  operation: null,
  sessions: [],
  signedIn: null,
  folder: "",
  instructions: "",
  projectId: null,
  snapshot: null,
  turns: [],
  files: null,
  note: "",
  handoffPrompt: "",
  setSessions: (sessions, signedIn) => set({ sessions, signedIn }),
  setFolder: (folder) => {
    if (get().operation) return;
    sessionGeneration += 1;
    filesGeneration += 1;
    set({ folder, files: null });
  },
  setInstructions: (instructions) => { if (!get().operation) set({ instructions }); },
  setProjectId: (projectId) => {
    if (get().operation) return;
    sessionGeneration += 1;
    set({ projectId });
  },
  setSnapshot: (snapshot) => set({ snapshot }),
  setTurns: (turns) => set((state) => ({ turns: typeof turns === "function" ? turns(state.turns) : turns })),
  setFiles: (files) => set({ files }),
  setNote: (note) => set({ note }),
  resetSession: () => {
    if (get().operation) return;
    sessionGeneration += 1;
    filesGeneration += 1;
    listGeneration += 1;
    set({ snapshot: null, turns: [], files: null, note: "" });
  },
}));

export function beginBuildOperation() {
  if (useBuild.getState().operation) return null;
  sessionGeneration += 1;
  filesGeneration += 1;
  const controller = new AbortController();
  useBuild.setState({ operation: controller });
  return controller;
}

export function isBuildOperation(controller: AbortController) {
  return useBuild.getState().operation === controller && !controller.signal.aborted;
}

export async function refreshBuildSessions() {
  const generation = ++listGeneration;
  try {
    const payload = await api.buildSessions();
    if (generation !== listGeneration) return;
    useBuild.getState().setSessions(payload.sessions || [], Boolean(payload.signedIn));
  } catch (error) {
    if (generation !== listGeneration) return;
    const message = error instanceof Error && error.message ? error.message : "Sessions did not load.";
    useBuild.getState().setNote(message);
  }
}

export async function openBuildSession(session: CliSession) {
  if (useBuild.getState().operation) return;
  const generation = ++sessionGeneration;
  filesGeneration += 1;
  useBuild.setState({ note: "", files: null });
  try {
    const snap = await api.buildLoad(session.id, session.cwd);
    if (generation !== sessionGeneration) return;
    const folder = snap.session?.cwd || session.cwd;
    useBuild.setState({ snapshot: snap, turns: snap.messages || [], folder });
    void loadBuildFiles(folder);
  } catch (error) {
    if (generation !== sessionGeneration) return;
    useBuild.getState().setNote(error instanceof Error && error.message ? error.message : "Session did not load.");
  }
}

export async function loadBuildFiles(path: string) {
  const generation = ++filesGeneration;
  if (!path) {
    useBuild.getState().setFiles(null);
    return;
  }
  try {
    const files = await api.buildFiles(path);
    if (generation === filesGeneration) useBuild.getState().setFiles(files);
  } catch {
    if (generation === filesGeneration) useBuild.getState().setFiles(null);
  }
}
