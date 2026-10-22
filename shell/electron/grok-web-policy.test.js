"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { isGrokUrl, isWorkspaceUrl, webStateForUrl } = require("./grok-web-policy");

test("Grok web policy permits Grok and the supported identity hosts", () => {
  assert.equal(isWorkspaceUrl("https://grok.com/"), true);
  assert.equal(isWorkspaceUrl("https://accounts.google.com/o/oauth2/auth"), true);
  assert.equal(isGrokUrl("https://www.grok.com/chat"), true);
});

test("Grok web policy rejects credentials, local addresses, and arbitrary pages", () => {
  assert.equal(isWorkspaceUrl("http://grok.com/"), false);
  assert.equal(isWorkspaceUrl("https://user:pass@grok.com/"), false);
  assert.equal(isWorkspaceUrl("https://127.0.0.1/"), false);
  assert.equal(isWorkspaceUrl("https://example.com/"), false);
  assert.equal(isGrokUrl("https://accounts.google.com/"), false);
});

test("Grok web state remains signing-in while an identity provider is open", () => {
  assert.equal(webStateForUrl("https://accounts.google.com/o/oauth2/auth"), "signing-in");
  assert.equal(webStateForUrl("https://grok.com/"), "ready");
  assert.equal(webStateForUrl("https://example.com/"), "error");
});
