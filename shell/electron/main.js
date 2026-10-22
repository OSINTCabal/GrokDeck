// GrokDeck Electron shell. The window is frameless; the page draws the title bar
// when window.grokdeck exists. Launch: electron /path/to/shell/electron

const {
  app, BrowserWindow, ipcMain, screen, dialog, session, shell,
  Tray, Menu, nativeImage, Notification, globalShortcut,
} = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const { localFromProtocol: protocolRoute } = require("./protocol");
const { tileForDrag, tileForSeam, tileForWindowSeam, tilesForArea, sameRect } = require("./tile");
const { createGrokWeb } = require("./grok-web");

const PORT = process.env.GROKDECK_PORT || "9488";
const APP_URL = process.env.GROKDECK_URL || `http://127.0.0.1:${PORT}`;
const APP_ORIGIN = new URL(APP_URL).origin;

if (process.env.GROKDECK_USER_DATA) {
  app.setPath("userData", process.env.GROKDECK_USER_DATA);
}
app.setName("grokdeck");
app.setDesktopName("grokdeck");
// Fedora's native Wayland path never starts a frameless title-bar drag, and
// Mutter discards the position half of setBounds. XWayland honors both.
if (process.platform === "linux") {
  app.commandLine.appendSwitch("ozone-platform", "x11");
  app.commandLine.appendSwitch("ozone-platform-hint", "x11");
}

let win = null;
let tray = null;
const grokWeb = createGrokWeb(() => win);

function localFromProtocol(raw) {
  return protocolRoute(raw, APP_URL);
}

function stateFile() {
  return path.join(app.getPath("userData"), "window-state.json");
}

function readWindowState() {
  try {
    const raw = JSON.parse(fs.readFileSync(stateFile(), "utf8"));
    const displays = screen.getAllDisplays();
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const display of displays) {
      const area = display.workArea;
      minX = Math.min(minX, area.x);
      minY = Math.min(minY, area.y);
      maxX = Math.max(maxX, area.x + area.width);
      maxY = Math.max(maxY, area.y + area.height);
    }
    const spanW = Math.max(720, maxX - minX);
    const spanH = Math.max(480, maxY - minY);
    const width = Math.min(Math.max(Number(raw.width) || 1280, 720), spanW);
    const height = Math.min(Math.max(Number(raw.height) || 800, 480), spanH);
    let x = Number.isFinite(raw.x) ? raw.x : minX + Math.round((spanW - width) / 2);
    let y = Number.isFinite(raw.y) ? raw.y : minY + Math.round((spanH - height) / 2);
    x = Math.min(Math.max(x, minX), maxX - width);
    y = Math.min(Math.max(y, minY), maxY - height);
    const savedFloat = raw.float;
    if (savedFloat && [savedFloat.x, savedFloat.y, savedFloat.width, savedFloat.height].every(Number.isFinite)) {
      floatBounds = {
        x: savedFloat.x,
        y: savedFloat.y,
        width: savedFloat.width,
        height: savedFloat.height,
      };
    }
    return {
      width,
      height,
      x,
      y,
      maximized: Boolean(raw.maximized),
    };
  } catch {
    return null;
  }
}

let stableBounds = null;
let floatBounds = null;
let closing = false;
let boundsReady = false;

function writeState(data) {
  try {
    fs.mkdirSync(path.dirname(stateFile()), { recursive: true });
    fs.writeFileSync(stateFile(), JSON.stringify(data));
  } catch {
    /* the next launch uses the default size */
  }
}

function saveWindowState() {
  // Closing reports a shrinking frame. Keep the last real size instead.
  if (closing) {
    if (stableBounds) writeState(stableBounds);
    return;
  }
  if (!boundsReady || !win || win.isDestroyed() || gripDrag || moveDrag) return;
  const bounds = win.getNormalBounds();
  stableBounds = { ...bounds, maximized: fillsWorkArea() };
  if (floatBounds) stableBounds.float = floatBounds;
  writeState(stableBounds);
}

function trustedSender(event) {
  return event.sender === win?.webContents && event.senderFrame?.origin === APP_ORIGIN;
}

function handle(channel, handler) {
  ipcMain.handle(channel, (event, ...args) => {
    if (!trustedSender(event)) throw new Error("Untrusted desktop request");
    return handler(...args);
  });
}

// Move and resize samples are one-way. Waiting for a reply on each one is
// what made the window lag behind the pointer.
function listen(channel, handler) {
  ipcMain.on(channel, (event, ...args) => {
    if (!trustedSender(event)) return;
    handler(...args);
  });
}

function showWindow(target) {
  if (!win || win.isDestroyed()) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
  if (target) void win.loadURL(target);
}

function createTray() {
  const image = nativeImage.createFromPath(path.join(__dirname, "grokdeck-icon.png"));
  const icon = image.isEmpty() ? image : image.resize({ width: 16, height: 16 });
  tray = new Tray(icon);
  tray.setToolTip("GrokDeck");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Show GrokDeck", click: () => showWindow() },
    { label: "Quit", click: () => app.quit() },
  ]));
  tray.on("click", () => showWindow());
}

function reportWindow(reason) {
  if (!win || win.isDestroyed()) return;
  const bounds = win.getBounds();
  const cursor = screen.getCursorScreenPoint();
  const scale = screen.getPrimaryDisplay().scaleFactor;
  console.log(`[grokdeck] ${reason} visible=${win.isVisible() ? 1 : 0} ${bounds.x},${bounds.y} ${bounds.width}x${bounds.height} cursor=${cursor.x},${cursor.y} scale=${scale}`);
}

function createWindow() {
  const saved = readWindowState();
  if (saved) {
    stableBounds = {
      x: saved.x,
      y: saved.y,
      width: saved.width,
      height: saved.height,
      maximized: Boolean(saved.maximized),
    };
  }
  const workArea = screen.getPrimaryDisplay().workAreaSize;
  win = new BrowserWindow({
    width: saved?.width || Math.min(1440, workArea.width),
    height: saved?.height || Math.min(900, workArea.height),
    x: saved?.x,
    y: saved?.y,
    minWidth: 720,
    minHeight: 480,
    frame: false,
    // Wayland draws a GTK shadow frame around frameless windows and that frame
    // takes the clicks meant for the title bar and the edge grips.
    hasShadow: process.platform !== "linux",
    backgroundColor: "#131211",
    resizable: true,
    title: "GrokDeck",
    icon: path.join(__dirname, "grokdeck-icon.png"),
    // Wayland often never emits ready-to-show for a hidden window, which left
    // the process up with a renderer and no window on screen.
    show: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  if (!saved) win.center();
  // Wayland owns maximized windows and will not move them. Keep the saved
  // size on Linux so the title bar can drag the window.
  if (saved?.maximized && process.platform !== "linux") win.maximize();
  else if (stableBounds) stableBounds = { ...stableBounds, maximized: false };
  win.once("ready-to-show", () => {
    if (!win || win.isDestroyed()) return;
    win.show();
    win.focus();
    reportWindow("ready-to-show");
  });
  setTimeout(() => {
    if (!win || win.isDestroyed()) return;
    if (!win.isVisible()) win.show();
    win.focus();
    for (const display of screen.getAllDisplays()) {
      const area = display.workArea;
      console.log(`[grokdeck] display work=${area.x},${area.y} ${area.width}x${area.height}`);
    }
    reportWindow("shown");
    setTimeout(() => {
      if (!win || win.isDestroyed()) return;
      boundsReady = true;
      if (!fitting) saveWindowState();
      reportWindow("settled");
    }, 700);
  }, 800);

  let tries = 0;
  win.webContents.on("did-finish-load", () => reportWindow("page-loaded"));
  win.webContents.on("did-fail-load", (_event, errorCode) => {
    if (errorCode === -3) return;
    console.log(`[grokdeck] load-failed code=${errorCode} try=${tries}`);
    if (tries++ < 60 && win && !win.isDestroyed()) {
      setTimeout(() => win && !win.isDestroyed() && win.loadURL(APP_URL), 500);
    }
  });
  win.webContents.on("will-navigate", (event, target) => {
    try {
      if (new URL(target).origin !== APP_ORIGIN) event.preventDefault();
    } catch {
      event.preventDefault();
    }
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    try {
      if (new URL(url).protocol === "https:") void shell.openExternal(url);
    } catch {
      /* invalid link */
    }
    return { action: "deny" };
  });
  win.loadURL(APP_URL);
  win.on("close", () => {
    closing = true;
    if (stableBounds) writeState(stableBounds);
  });
  win.on("resized", () => {
    if (closing || gripDrag || moveDrag || fitting || Date.now() < quietUntil) return;
    const bounds = win.getBounds();
    if (!matchTile(bounds)) rememberFloat(bounds);
    saveWindowState();
  });
  win.on("moved", () => {
    if (closing || fitting || suppressMoveSave || moveDrag || gripDrag) return;
    saveWindowState();
  });
  win.on("closed", () => {
    grokWeb.destroy();
    win = null;
  });
}

handle("window:minimize", () => win && win.minimize());
function workArea() {
  if (!win || win.isDestroyed()) return screen.getPrimaryDisplay().workArea;
  return screen.getDisplayMatching(win.getBounds()).workArea;
}

function fillsWorkArea() {
  if (!win || win.isDestroyed()) return false;
  if (win.isMaximized()) return true;
  const area = workArea();
  const bounds = win.getBounds();
  return Math.abs(bounds.x - area.x) <= 2
    && Math.abs(bounds.y - area.y) <= 2
    && Math.abs(bounds.width - area.width) <= 2
    && Math.abs(bounds.height - area.height) <= 2;
}

handle("window:toggle-maximize", () => {
  if (!win) return;
  if (process.platform === "linux") {
    const area = workArea();
    suppressMoveSave = true;
    if (fillsWorkArea()) {
      const back = restoredFloat(area, null);
      const next = { x: back.x, y: back.y, width: back.width, height: back.height, maximized: false };
      win.setBounds(next);
      rememberFloat(next);
      stableBounds = { ...next, float: floatBounds };
      writeState(stableBounds);
    } else {
      rememberFloat(win.getBounds());
      stableBounds = { ...floatBounds, maximized: true, float: floatBounds };
      writeState(stableBounds);
      win.setBounds({ x: area.x, y: area.y, width: area.width, height: area.height });
    }
    setTimeout(() => {
      suppressMoveSave = false;
    }, 400);
    return;
  }
  if (win.isMaximized()) win.unmaximize();
  else win.maximize();
});
handle("window:close", () => win && win.close());

const RESIZE_EDGES = new Set(["n", "s", "e", "w", "ne", "nw", "se", "sw"]);
const MIN_WIDTH = 720;
const MIN_HEIGHT = 480;
let resizeOrigin = null;
let moveOrigin = null;
let resizeCursor = null;
let pendingResize = null;
let resizeScheduled = false;
let moveCursor = null;
let gripDrag = false;
let moveDrag = false;
let fitting = false;
let suppressMoveSave = false;
let quietUntil = 0;

function rememberFloat(bounds) {
  if (!bounds || matchTile(bounds) || nearFill(bounds)) return;
  floatBounds = {
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
  };
}

// A window that still covers almost the whole monitor is maximized, even if
// the height is a few pixels short. It must not become the restored size.
function nearFill(bounds) {
  if (!bounds || !Number.isFinite(bounds.width)) return false;
  const area = screen.getDisplayMatching(bounds).workArea;
  return bounds.width >= area.width - 80 || bounds.height >= area.height - 40;
}

function restoredFloat(area, cursor) {
  const saved = floatBounds && !nearFill(floatBounds) ? floatBounds : null;
  const width = saved ? saved.width : Math.min(1056, Math.max(MIN_WIDTH, area.width - 160));
  const height = saved ? saved.height : Math.min(760, Math.max(MIN_HEIGHT, area.height - 200));
  if (!cursor) {
    return {
      x: saved ? saved.x : area.x + Math.round((area.width - width) / 2),
      y: saved ? saved.y : area.y + 48,
      width,
      height,
    };
  }
  return clampRect({
    x: cursor.x - width / 2,
    y: cursor.y - 24,
    width,
    height,
  });
}

function matchTile(bounds) {
  if (!bounds || !Number.isFinite(bounds.width)) return false;
  const area = screen.getDisplayMatching(bounds).workArea;
  return tilesForArea(area).some((tile) => sameRect(bounds, tile, 8));
}

// Fedora tiles from the pointer only. The monitor under the cursor is the
// one that tiles. A drag that starts in the top band does not fill it.
function workAreas() {
  return screen.getAllDisplays().map((display) => display.workArea);
}

function edgeSnapTarget(startCursor, startedBounds) {
  if (!win || win.isDestroyed()) return null;
  const cursor = screen.getCursorScreenPoint();
  if (!cursor || (cursor.x === 0 && cursor.y === 0)) return null;
  const areas = workAreas();
  const seam = tileForSeam(cursor, startCursor, areas)
    || tileForWindowSeam(win.getBounds(), startedBounds, areas);
  if (seam) return seam;
  const area = screen.getDisplayNearestPoint(cursor).workArea;
  return tileForDrag({
    cursor,
    start: startCursor,
    bounds: win.getBounds(),
    area,
  });
}

function applyEdgeSnap(startCursor, startedBounds) {
  const target = edgeSnapTarget(startCursor, startedBounds);
  if (!target || !win || win.isDestroyed()) return false;
  const bounds = win.getBounds();
  if (sameRect(bounds, target, 8)) return false;
  fitting = true;
  quietUntil = Date.now() + 400;
  win.setBounds({ x: target.x, y: target.y, width: target.width, height: target.height });
  stableBounds = {
    x: target.x,
    y: target.y,
    width: target.width,
    height: target.height,
    maximized: Boolean(target.maximized),
    float: floatBounds,
  };
  if (boundsReady) writeState(stableBounds);
  setTimeout(() => {
    fitting = false;
  }, 400);
  reportWindow(target.maximized ? "snap-fill" : "snap-tile");
  return true;
}

function applyEdge(origin, edge, dx, dy) {
  let { x, y, width, height } = origin;
  if (edge.includes("e")) width = Math.max(MIN_WIDTH, origin.width + dx);
  if (edge.includes("s")) height = Math.max(MIN_HEIGHT, origin.height + dy);
  if (edge.includes("w")) {
    width = Math.max(MIN_WIDTH, origin.width - dx);
    x = origin.x + (origin.width - width);
  }
  if (edge.includes("n")) {
    height = Math.max(MIN_HEIGHT, origin.height - dy);
    y = origin.y + (origin.height - height);
  }
  return {
    x: Math.round(x),
    y: Math.round(y),
    width: Math.round(width),
    height: Math.round(height),
  };
}

function releaseLinuxMaximize() {
  if (process.platform === "linux" && win && !win.isDestroyed() && win.isMaximized()) {
    win.unmaximize();
  }
}

// The OS cursor is the position that matters. Renderer totals are only a
// fallback, and a huge fallback while the cursor has not moved is a stale
// sample (one of those flung the window off screen). Drop it.
function gestureDelta(originCursor, fallbackX, fallbackY) {
  if (originCursor) {
    const cursor = screen.getCursorScreenPoint();
    const dx = cursor.x - originCursor.x;
    const dy = cursor.y - originCursor.y;
    if (dx !== 0 || dy !== 0) return { dx, dy };
  }
  if (Math.abs(fallbackX) > 200 || Math.abs(fallbackY) > 200) return null;
  return { dx: fallbackX, dy: fallbackY };
}

listen("window:resize-start", () => {
  if (!win || win.isDestroyed()) {
    resizeOrigin = null;
    resizeCursor = null;
    gripDrag = false;
    return false;
  }
  releaseLinuxMaximize();
  if (process.platform !== "linux" && win.isMaximized()) {
    resizeOrigin = null;
    resizeCursor = null;
    gripDrag = false;
    return false;
  }
  gripDrag = true;
  resizeOrigin = win.getBounds();
  resizeCursor = screen.getCursorScreenPoint();
  return true;
});
function performResize(edge, dx, dy) {
  if (!win || win.isDestroyed() || !resizeOrigin || !RESIZE_EDGES.has(edge)) return false;
  const delta = gestureDelta(resizeCursor, dx, dy);
  if (!delta) return false;
  const clampedX = Math.max(-4000, Math.min(4000, delta.dx));
  const clampedY = Math.max(-4000, Math.min(4000, delta.dy));
  const next = applyEdge(resizeOrigin, edge, clampedX, clampedY);
  if (!sameRect(win.getBounds(), next, 0)) win.setBounds(next);
  return true;
}
listen("window:resize-by", (edge, dx, dy) => {
  const nextX = Number(dx);
  const nextY = Number(dy);
  if (!resizeOrigin || !RESIZE_EDGES.has(edge) || !Number.isFinite(nextX) || !Number.isFinite(nextY)) return;
  pendingResize = { edge, dx: nextX, dy: nextY };
  if (resizeScheduled) return;
  resizeScheduled = true;
  setImmediate(() => {
    resizeScheduled = false;
    const sample = pendingResize;
    pendingResize = null;
    if (sample) performResize(sample.edge, sample.dx, sample.dy);
  });
});
listen("window:resize-end", () => {
  if (pendingResize) {
    const sample = pendingResize;
    pendingResize = null;
    performResize(sample.edge, sample.dx, sample.dy);
  }
  gripDrag = false;
  resizeOrigin = null;
  resizeCursor = null;
  boundsReady = true;
  quietUntil = Date.now() + 300;
  if (win && !win.isDestroyed()) {
    const bounds = win.getBounds();
    if (!matchTile(bounds)) rememberFloat(bounds);
    saveWindowState();
  }
  reportWindow("resize-end");
  return true;
});
function clampRect(rect) {
  let x = Math.round(rect.x);
  let y = Math.round(rect.y);
  const width = Math.round(rect.width);
  const height = Math.round(rect.height);
  const displays = screen.getAllDisplays();
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const display of displays) {
    const area = display.workArea;
    minX = Math.min(minX, area.x);
    minY = Math.min(minY, area.y);
    maxX = Math.max(maxX, area.x + area.width);
    maxY = Math.max(maxY, area.y + area.height);
  }
  if (Number.isFinite(minX)) {
    const margin = 240;
    x = Math.max(minX - width + margin, Math.min(x, maxX - margin));
    y = Math.max(minY, Math.min(y, maxY - margin));
  }
  return { x, y, width, height };
}
// Keep the title bar below a panel when the window crosses onto that monitor.
// The size stays what the user made it.
function fitAcrossDisplays(rect) {
  const displays = screen.getAllDisplays();
  let { x, y, width, height } = rect;
  let minTop = -Infinity;
  let overlapped = false;
  for (const display of displays) {
    const area = display.workArea;
    const overlaps = x < area.x + area.width && x + width > area.x;
    if (!overlaps) continue;
    overlapped = true;
    minTop = Math.max(minTop, area.y);
  }
  if (overlapped && y < minTop) y = minTop;
  return { x, y, width, height };
}

function placedMove(origin, dx, dy) {
  return clampRect(fitAcrossDisplays({
    x: origin.x + dx,
    y: origin.y + dy,
    width: origin.width,
    height: origin.height,
  }));
}

listen("window:move-start", () => {
  if (!win || win.isDestroyed()) {
    moveOrigin = null;
    moveCursor = null;
    moveDrag = false;
    return false;
  }
  releaseLinuxMaximize();
  if (process.platform !== "linux" && win.isMaximized()) {
    moveOrigin = null;
    moveCursor = null;
    moveDrag = false;
    return false;
  }
  const bounds = win.getBounds();
  moveCursor = screen.getCursorScreenPoint();
  if (matchTile(bounds) || nearFill(bounds)) {
    const restored = restoredFloat(screen.getDisplayMatching(bounds).workArea, moveCursor);
    suppressMoveSave = true;
    win.setBounds(restored);
    moveOrigin = restored;
    setTimeout(() => {
      suppressMoveSave = false;
    }, 400);
  } else {
    rememberFloat(bounds);
    moveOrigin = bounds;
  }
  moveDrag = true;
  return true;
});
let pendingMove = null;
let moveScheduled = false;

function performMove(dx, dy) {
  if (!win || win.isDestroyed() || !moveOrigin || !moveCursor) return;
  const delta = gestureDelta(moveCursor, dx, dy);
  if (!delta) return;
  const clampedX = Math.max(-8000, Math.min(8000, delta.dx));
  const clampedY = Math.max(-8000, Math.min(8000, delta.dy));
  const desired = placedMove(moveOrigin, clampedX, clampedY);
  const seamTile = tileForWindowSeam(desired, moveOrigin, workAreas());
  const next = seamTile || desired;
  const current = win.getBounds();
  if (current.x === next.x && current.y === next.y && current.width === next.width && current.height === next.height) return;
  if (current.width !== next.width || current.height !== next.height) win.setBounds(next);
  else win.setPosition(next.x, next.y);
}

listen("window:move-by", (dx, dy) => {
  const nextX = Number(dx);
  const nextY = Number(dy);
  if (!Number.isFinite(nextX) || !Number.isFinite(nextY)) return;
  // Keep only the newest sample. A queue of setBounds calls is the drag lag.
  pendingMove = { dx: nextX, dy: nextY };
  if (moveScheduled) return;
  moveScheduled = true;
  setImmediate(() => {
    moveScheduled = false;
    const sample = pendingMove;
    pendingMove = null;
    if (sample) performMove(sample.dx, sample.dy);
  });
});
listen("window:move-end", () => {
  if (pendingMove) {
    const sample = pendingMove;
    pendingMove = null;
    performMove(sample.dx, sample.dy);
  }
  const started = moveOrigin;
  const startedCursor = moveCursor;
  moveDrag = false;
  moveOrigin = null;
  moveCursor = null;
  boundsReady = true;
  quietUntil = Date.now() + 300;
  if (started) rememberFloat(started);
  const snapped = applyEdgeSnap(startedCursor, started);
  if (!snapped && win && !win.isDestroyed()) saveWindowState();
  reportWindow("move-end");
  return true;
});
handle("window:background", (color) => {
  if (!win || win.isDestroyed() || !/^#[0-9a-fA-F]{6}$/.test(String(color))) return false;
  win.setBackgroundColor(String(color));
  return true;
});
handle("grok-web:open", (rectangle, theme) => grokWeb.open(rectangle, theme));
handle("grok-web:resize", (rectangle) => grokWeb.resize(rectangle));
handle("grok-web:close", () => {
  grokWeb.close();
  return true;
});
handle("grok-web:refresh", () => grokWeb.refresh());
handle("grok-web:new-chat", () => grokWeb.newChat());
handle("grok-web:sign-out", () => grokWeb.signOut());
handle("notify", (title, body) => {
  if (!Notification.isSupported()) return false;
  const note = new Notification({
    title: String(title).slice(0, 80) || "GrokDeck",
    body: String(body).slice(0, 180),
  });
  note.show();
  return true;
});
handle("grokdeck:open-grok-bot", () => {
  const child = spawn("gtk-launch", ["grok-bot"], { detached: true, stdio: "ignore" });
  child.on("error", () => {
    const direct = spawn("grok-bot", [], { detached: true, stdio: "ignore" });
    direct.unref();
  });
  child.unref();
  return true;
});
handle("grokdeck:pick-directory", async () => {
  if (!win || win.isDestroyed()) return null;
  const result = await dialog.showOpenDialog(win, {
    title: "Working directory",
    properties: ["openDirectory"],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", (_event, argv) => {
    const link = argv.find((arg) => String(arg).startsWith("grokdeck:"));
    showWindow(link ? localFromProtocol(link) : null);
  });

  try {
    if (process.defaultApp && process.argv.length >= 2) {
      app.setAsDefaultProtocolClient("grokdeck", process.execPath, [path.resolve(process.argv[1])]);
    } else {
      app.setAsDefaultProtocolClient("grokdeck");
    }
  } catch {
    /* protocol registration is optional */
  }

  app.whenReady().then(() => {
    const allowMic = (permission) => permission === "media" || permission === "microphone" || permission === "audioCapture";
    session.defaultSession.setPermissionRequestHandler((contents, permission, callback) => {
      let local = false;
      try {
        local = new URL(contents.getURL()).origin === APP_ORIGIN;
      } catch {
        local = false;
      }
      callback(local && allowMic(permission));
    });
    createWindow();
    try {
      createTray();
    } catch {
      tray = null;
    }
    try {
      globalShortcut.register("CommandOrControl+Alt+G", () => {
        if (!win) return;
        if (win.isVisible() && win.isFocused()) win.hide();
        else showWindow();
      });
    } catch {
      /* shortcut already taken */
    }
  });

  app.on("will-quit", () => {
    globalShortcut.unregisterAll();
  });
  app.on("window-all-closed", () => app.quit());
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
}

module.exports = { localFromProtocol };
