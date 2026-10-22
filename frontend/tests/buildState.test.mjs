import assert from "node:assert/strict";
import test from "node:test";
import { api } from "../src/lib/api.ts";
import { beginBuildOperation, isBuildOperation, loadBuildFiles, openBuildSession, refreshBuildSessions, useBuild } from "../src/lib/buildState.ts";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const session = (id) => ({ id, cwd: `/tmp/${id}`, title: id });
const snapshot = (id) => ({ session: session(id), messages: [{ id, text: id }], model: "saved-model", effort: "low" });
function reset() {
  useBuild.setState({ operation: null });
  useBuild.getState().resetSession();
}

test("only the latest session load applies, even when the first finishes last", async () => {
  reset();
  const first = deferred(), second = deferred();
  api.buildLoad = (id) => (id === "one" ? first : second).promise;
  api.buildFiles = async (cwd) => ({ cwd });
  const a = openBuildSession(session("one"));
  const b = openBuildSession(session("two"));
  second.resolve(snapshot("two"));
  await b;
  first.resolve(snapshot("one"));
  await a;
  assert.equal(useBuild.getState().snapshot.session.id, "two");
  assert.equal(useBuild.getState().folder, "/tmp/two");
  assert.equal(useBuild.getState().snapshot.model, "saved-model");
  assert.equal(useBuild.getState().snapshot.effort, "low");
});

test("reset invalidates outstanding session, file and session-list results", async () => {
  reset();
  const load = deferred(), files = deferred(), list = deferred();
  api.buildLoad = () => load.promise;
  api.buildFiles = () => files.promise;
  api.buildSessions = () => list.promise;
  const tasks = [openBuildSession(session("late")), loadBuildFiles("/tmp/late"), refreshBuildSessions()];
  useBuild.setState({ sessions: [] });
  useBuild.getState().resetSession();
  load.resolve(snapshot("late"));
  files.resolve({ cwd: "/tmp/late" });
  list.resolve({ sessions: [session("late")], signedIn: true });
  await Promise.all(tasks);
  assert.equal(useBuild.getState().snapshot, null);
  assert.equal(useBuild.getState().files, null);
  assert.deepEqual(useBuild.getState().sessions, []);
});

test("obsolete failures cannot clear newer files or overwrite session errors", async () => {
  reset();
  const first = deferred(), second = deferred();
  api.buildFiles = (path) => (path === "one" ? first : second).promise;
  const a = loadBuildFiles("one"), b = loadBuildFiles("two");
  second.resolve({ cwd: "two" });
  await b;
  first.reject(new Error("old error"));
  await a;
  assert.equal(useBuild.getState().files.cwd, "two");
  api.buildLoad = async () => { throw new Error("Load failed safely"); };
  await openBuildSession(session("failed"));
  assert.equal(useBuild.getState().note, "Load failed safely");
});

test("active operations block context changes and invalidate pending session loads", async () => {
  reset();
  const load = deferred();
  api.buildLoad = () => load.promise;
  const task = openBuildSession(session("late"));
  const state = useBuild.getState();
  const originalFolder = state.folder;
  const originalProject = state.projectId;
  const operation = beginBuildOperation();
  assert.ok(operation);
  assert.equal(beginBuildOperation(), null);
  state.setFolder("wrong");
  state.setProjectId("wrong");
  state.resetSession();
  await openBuildSession(session("wrong"));
  load.resolve(snapshot("late"));
  await task;
  assert.equal(useBuild.getState().snapshot, null);
  assert.equal(useBuild.getState().folder, originalFolder);
  assert.equal(useBuild.getState().projectId, originalProject);
  assert.equal(isBuildOperation(operation), true);
  operation.abort();
  assert.equal(isBuildOperation(operation), false);
  assert.equal(beginBuildOperation(), null, "aborted operation stays locked until its work settles");
  reset();
});

test("session lists use the newest refresh and handle current failures", async () => {
  reset();
  const first = deferred(), second = deferred();
  let calls = 0;
  api.buildSessions = () => (++calls === 1 ? first : second).promise;
  const a = refreshBuildSessions(), b = refreshBuildSessions();
  second.resolve({ sessions: [session("new")], signedIn: true });
  await b;
  first.reject(new Error("stale list failure"));
  await a;
  assert.equal(useBuild.getState().sessions[0].id, "new");
  assert.equal(useBuild.getState().note, "");
  api.buildSessions = async () => { throw new Error("Current list failure"); };
  await refreshBuildSessions();
  assert.equal(useBuild.getState().note, "Current list failure");
});
