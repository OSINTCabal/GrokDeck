"use strict";

const { WebContentsView, dialog, session, shell } = require("electron");
const { isGrokUrl, isWorkspaceUrl, parseHttpsUrl, webStateForUrl } = require("./grok-web-policy");

const CHAT_URL = "https://grok.com/";
const PARTITION = "persist:grokdeck-web";
const REQUESTABLE_PERMISSIONS = new Set(["media", "notifications", "clipboard-read"]);

function safeBounds(value, contentSize, zoom = 1) {
  if (!value || !["x", "y", "width", "height"].every((key) => Number.isFinite(value[key]))) {
    throw new Error("Invalid workspace bounds");
  }
  const factor = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  const x = Math.max(0, Math.min(contentSize[0], Math.round(value.x * factor)));
  const y = Math.max(0, Math.min(contentSize[1], Math.round(value.y * factor)));
  const width = Math.max(0, Math.min(contentSize[0] - x, Math.round(value.width * factor)));
  const height = Math.max(0, Math.min(contentSize[1] - y, Math.round(value.height * factor)));
  return { x, y, width, height };
}

function createGrokWeb(getWindow, electron = { WebContentsView, dialog, session, shell }) {
  const { WebContentsView, dialog, session, shell, setTimeout: scheduleTimeout = setTimeout, clearTimeout: cancelTimeout = clearTimeout } = electron;
  let view = null;
  let lastBounds = null;
  let generation = 0;
  let attached = false;
  let loaded = false;
  let navigationGeneration = 0;
  let permissionSession = null;

  function notify(status, detail = "") {
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send("grok-web:state", { status, detail });
  }

  function notifyCurrentState(contents) {
    const status = webStateForUrl(contents.getURL());
    notify(status, status === "signing-in" ? "Finish signing in to Grok Chat in this window." : "");
  }

  function detach() {
    const win = getWindow();
    if (attached && view && win && !win.isDestroyed()) win.contentView.removeChildView(view);
    attached = false;
    if (view && !view.webContents.isDestroyed()) view.setVisible(false);
  }

  function retireView() {
    const current = view;
    if (!current) return;
    const win = getWindow();
    if (attached && win && !win.isDestroyed()) win.contentView.removeChildView(current);
    attached = false;
    view = null;
    loaded = false;
    navigationGeneration = 0;
    if (!current.webContents.isDestroyed()) current.webContents.close();
  }

  async function requestPermission(contents, permission, callback, details) {
    if (!view || contents !== view.webContents || !REQUESTABLE_PERMISSIONS.has(permission)) {
      callback(false);
      return;
    }
    const url = details?.requestingUrl || contents.getURL();
    if (!isGrokUrl(url)) {
      callback(false);
      return;
    }
    const win = getWindow();
    if (!win || win.isDestroyed()) {
      callback(false);
      return;
    }
    const label = permission === "media" ? "your microphone or camera" : permission === "clipboard-read" ? "your clipboard" : "notifications";
    const answer = await dialog.showMessageBox(win, {
      type: "question",
      title: "Grok web permission",
      message: `Allow Grok to use ${label}?`,
      buttons: ["Deny", "Allow"],
      defaultId: 0,
      cancelId: 0,
    }).catch(() => ({ response: 0 }));
    callback(answer.response === 1 && contents === view?.webContents);
  }

  function configureSession(current) {
    if (permissionSession === current) return;
    permissionSession = current;
    current.setPermissionCheckHandler((contents, permission, requestingOrigin) => {
      return contents === view?.webContents && permission === "clipboard-sanitized-write" && isGrokUrl(requestingOrigin);
    });
    current.setPermissionRequestHandler((contents, permission, callback, details) => {
      void requestPermission(contents, permission, callback, details);
    });
    current.on("will-download", (event, item, contents) => {
      if (contents !== view?.webContents) {
        event.preventDefault();
        return;
      }
      item.once("done", (_event, state) => {
        if (contents !== view?.webContents) return;
        notify(state === "completed" ? "ready" : "error", state === "completed" ? "Download saved." : "Download failed.");
      });
    });
  }

  function createView() {
    if (view && !view.webContents.isDestroyed()) return view;
    const webSession = session.fromPartition(PARTITION);
    configureSession(webSession);
    const current = new WebContentsView({
      webPreferences: {
        session: webSession,
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        nodeIntegrationInWorker: false,
        webSecurity: true,
      },
    });
    view = current;
    current.setBackgroundColor("#131211");
    const contents = current.webContents;
    const active = () => view === current && !contents.isDestroyed();
    contents.on("did-start-navigation", (_event, url, _inPlace, mainFrame) => {
      if (!active()) return;
      if (mainFrame) {
        navigationGeneration = generation;
        notify(isGrokUrl(url) ? "loading" : "signing-in");
      }
    });
    contents.on("will-navigate", (event, url) => {
      if (!active()) return;
      if (!isWorkspaceUrl(url)) {
        event.preventDefault();
        notify("error", "That page cannot open inside GrokDeck.");
      }
    });
    contents.on("will-redirect", (event, url) => {
      if (!active()) return;
      if (!isWorkspaceUrl(url)) event.preventDefault();
    });
    contents.setWindowOpenHandler(({ url }) => {
      if (!active()) return { action: "deny" };
      if (isWorkspaceUrl(url)) {
        void contents.loadURL(url).catch(() => { if (active()) notify("error", "Grok could not open that page."); });
      } else {
        const parsed = parseHttpsUrl(url);
        if (parsed) void shell.openExternal(parsed.toString());
      }
      return { action: "deny" };
    });
    contents.on("did-finish-load", () => {
      if (!active() || navigationGeneration !== generation) return;
      loaded = true;
      notifyCurrentState(contents);
    });
    contents.on("did-fail-load", (_event, code, _description, _url, mainFrame) => {
      if (active() && mainFrame && code !== -3) {
        loaded = false;
        navigationGeneration = 0;
        notify("error", "Grok could not load. Check your connection and try again.");
      }
    });
    contents.on("render-process-gone", () => {
      if (!active()) return;
      generation += 1;
      retireView();
      notify("error", "The Grok page stopped. Reload it to continue.");
    });
    return current;
  }

  async function open(rectangle, theme = "dark") {
    const win = getWindow();
    if (!win || win.isDestroyed()) return false;
    const current = createView();
    const version = ++generation;
    lastBounds = rectangle;
    current.setBackgroundColor(theme === "light" ? "#f9f8f7" : "#131211");
    current.setBounds(safeBounds(rectangle, win.getContentSize(), win.webContents.getZoomFactor()));
    if (!attached) {
      win.contentView.addChildView(current);
      attached = true;
    }
    current.setVisible(true);
    if (!loaded) {
      notify("loading");
      try {
        navigationGeneration = version;
        await contentsLoad(current.webContents, CHAT_URL);
      } catch {
        if (version === generation) {
          generation += 1;
          retireView();
          notify("error", "Grok could not open. Check your connection and try again.");
        }
        return false;
      }
    }
    if (version === generation) {
      current.webContents.focus();
      notifyCurrentState(current.webContents);
    }
    return version === generation && view === current;
  }

  function contentsLoad(contents, url) {
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = scheduleTimeout(() => reject(new Error("Grok load timed out")), 30_000);
    });
    return Promise.race([contents.loadURL(url), timeout]).finally(() => cancelTimeout(timer));
  }

  function resize(rectangle) {
    lastBounds = rectangle;
    const win = getWindow();
    if (!view || !attached || !win || win.isDestroyed()) return false;
    view.setBounds(safeBounds(rectangle, win.getContentSize(), win.webContents.getZoomFactor()));
    return true;
  }

  function close() {
    generation += 1;
    detach();
  }

  async function refresh() {
    if (!view || view.webContents.isDestroyed()) return false;
    view.webContents.reloadIgnoringCache();
    return true;
  }

  async function newChat() {
    if (!view || view.webContents.isDestroyed()) return false;
    const version = ++generation;
    navigationGeneration = version;
    loaded = false;
    try {
      await contentsLoad(view.webContents, CHAT_URL);
      return version === generation;
    } catch {
      if (version === generation) {
        generation += 1;
        retireView();
        notify("error", "Grok could not open a new chat. Check your connection and try again.");
      }
      return false;
    }
  }

  async function signOut() {
    generation += 1;
    retireView();
    const webSession = session.fromPartition(PARTITION);
    await webSession.clearStorageData();
    await webSession.clearCache();
    await webSession.cookies.flushStore();
    notify("signed-out");
    return true;
  }

  function destroy() {
    generation += 1;
    retireView();
  }

  return { open, close, resize, refresh, newChat, signOut, destroy };
}

module.exports = { CHAT_URL, PARTITION, createGrokWeb, safeBounds };
