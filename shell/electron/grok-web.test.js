"use strict";

const { EventEmitter } = require("node:events");
const test = require("node:test");
const assert = require("node:assert/strict");
const { createGrokWeb } = require("./grok-web");

function fixture(timeoutLoads = 0) {
  const views = [];
  const webSession = new EventEmitter();
  webSession.setPermissionCheckHandler = () => {};
  webSession.setPermissionRequestHandler = () => {};
  webSession.clearStorageData = async () => { webSession.storageCleared = true; };
  webSession.clearCache = async () => { webSession.cacheCleared = true; };
  webSession.cookies = { flushStore: async () => { webSession.cookiesFlushed = true; } };
  class View {
    constructor() {
      this.webContents = new EventEmitter();
      this.webContents.url = "https://grok.com/";
      this.webContents.pending = [];
      this.webContents.isDestroyed = () => Boolean(this.webContents.destroyed);
      this.webContents.getURL = () => this.webContents.url;
      this.webContents.setWindowOpenHandler = () => {};
      this.webContents.focus = () => {};
      this.webContents.close = () => { this.webContents.destroyed = true; };
      this.webContents.loadURL = (url) => new Promise((resolve) => {
        this.webContents.url = url;
        this.webContents.pending.push(resolve);
      });
      views.push(this);
    }
    setBackgroundColor() {}
    setBounds() {}
    setVisible() {}
  }
  const states = [];
  let timeoutCalls = 0;
  const win = {
    isDestroyed: () => false,
    getContentSize: () => [1000, 800],
    webContents: { getZoomFactor: () => 1, send: (_channel, state) => states.push(state.status) },
    contentView: { addChildView() {}, removeChildView() {} },
  };
  return {
    controller: createGrokWeb(() => win, {
      WebContentsView: View,
      dialog: {},
      session: { fromPartition: () => webSession },
      shell: {},
      ...(timeoutLoads ? { setTimeout: (callback) => {
        timeoutCalls += 1;
        if (timeoutCalls <= timeoutLoads) queueMicrotask(callback);
        return timeoutCalls;
      }, clearTimeout: () => {} } : {}),
    }),
    states, views, webSession,
  };
}

test("sign-out retires an in-flight Grok page and ignores its late events", async () => {
  const { controller, states, views, webSession } = fixture();
  const opening = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  const old = views[0].webContents;
  await controller.signOut();
  old.emit("did-start-navigation", {}, "https://grok.com/", false, true);
  old.emit("did-finish-load");
  old.pending.shift()();
  assert.equal(await opening, false);
  assert.deepEqual(states, ["loading", "signed-out"]);
  assert.equal(webSession.storageCleared && webSession.cacheCleared && webSession.cookiesFlushed, true);
});

test("a fresh Grok view can load after sign-out", async () => {
  const { controller, states, views } = fixture();
  const first = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  await controller.signOut();
  views[0].webContents.pending.shift()();
  await first;
  const reopened = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  const current = views[1].webContents;
  current.emit("did-start-navigation", {}, "https://grok.com/", false, true);
  current.emit("did-finish-load");
  current.pending.shift()();
  assert.equal(await reopened, true);
  assert.equal(states.at(-1), "ready");
});

test("a timed-out Grok page cannot publish ready after it is retired", async () => {
  const { controller, states, views } = fixture(1);
  assert.equal(await controller.open({ x: 0, y: 0, width: 800, height: 600 }), false);
  const old = views[0].webContents;
  old.emit("did-start-navigation", {}, "https://grok.com/", false, true);
  old.emit("did-finish-load");
  assert.deepEqual(states, ["loading", "error"]);
});

test("a timed-out Grok page can be opened again", async () => {
  const { controller, views } = fixture(1);
  assert.equal(await controller.open({ x: 0, y: 0, width: 800, height: 600 }), false);
  const reopened = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  views[1].webContents.pending.shift()();
  assert.equal(await reopened, true);
});

test("download completion from a retired Grok page cannot overwrite current state", async () => {
  const { controller, states, views, webSession } = fixture();
  const opening = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  const old = views[0].webContents;
  old.pending.shift()();
  await opening;
  const item = new EventEmitter();
  webSession.emit("will-download", { preventDefault() { throw new Error("active download blocked"); } }, item, old);
  await controller.signOut();
  item.emit("done", {}, "completed");
  assert.equal(states.at(-1), "signed-out");
});

test("download completion from an old Grok page cannot alter a replacement page", async () => {
  const { controller, states, views, webSession } = fixture();
  const opening = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  const old = views[0].webContents;
  old.pending.shift()();
  await opening;
  const item = new EventEmitter();
  webSession.emit("will-download", { preventDefault() { throw new Error("active download blocked"); } }, item, old);
  await controller.signOut();
  const reopened = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  views[1].webContents.pending.shift()();
  await reopened;
  const statesBeforeDone = states.length;
  item.emit("done", {}, "completed");
  assert.equal(states.length, statesBeforeDone);
});

test("download completion from the active Grok page reports its result", async () => {
  const { controller, states, views, webSession } = fixture();
  const opening = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  const current = views[0].webContents;
  current.pending.shift()();
  await opening;
  const item = new EventEmitter();
  webSession.emit("will-download", { preventDefault() { throw new Error("active download blocked"); } }, item, current);
  const beforeDone = states.length;
  item.emit("done", {}, "completed");
  assert.equal(states.length, beforeDone + 1);
  assert.equal(states.at(-1), "ready");
});

test("a renderer crash retires its view and retry actually loads a replacement", async () => {
  const { controller, states, views } = fixture();
  const opening = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  const old = views[0].webContents;
  old.emit("did-finish-load");
  old.pending.shift()();
  await opening;
  old.emit("render-process-gone");
  assert.equal(old.destroyed, true);
  assert.equal(states.at(-1), "error");
  const beforeLateEvents = states.length;
  old.emit("did-finish-load");
  assert.equal(states.length, beforeLateEvents);
  const retry = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  assert.equal(views.length, 2);
  assert.equal(views[1].webContents.pending.length, 1);
  views[1].webContents.emit("did-finish-load");
  views[1].webContents.pending.shift()();
  assert.equal(await retry, true);
  assert.equal(states.at(-1), "ready");
});

test("failed main-frame navigation invalidates the loaded page so retry loads again", async () => {
  const { controller, states, views } = fixture();
  const opening = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  const current = views[0].webContents;
  current.emit("did-finish-load");
  current.pending.shift()();
  await opening;
  current.emit("did-fail-load", {}, -105, "offline", "https://grok.com/", true);
  assert.equal(states.at(-1), "error");
  current.emit("did-finish-load");
  assert.equal(states.at(-1), "error");
  const retry = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  assert.equal(current.pending.length, 1);
  current.emit("did-finish-load");
  current.pending.shift()();
  assert.equal(await retry, true);
});

test("a crash during loading cannot publish ready from its late load promise", async () => {
  const { controller, states, views } = fixture();
  const opening = controller.open({ x: 0, y: 0, width: 800, height: 600 });
  const old = views[0].webContents;
  old.emit("render-process-gone");
  old.pending.shift()();
  assert.equal(await opening, false);
  assert.equal(states.at(-1), "error");
});
