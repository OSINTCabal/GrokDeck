"use strict";

// Only top-level identity and Grok pages may replace the embedded workspace.
// Grok itself still loads its required third-party subresources normally.
const WEB_ORIGINS = new Set([
  "https://grok.com",
  "https://www.grok.com",
  "https://x.com",
  "https://www.x.com",
  "https://auth.x.ai",
  "https://accounts.x.ai",
  "https://accounts.google.com",
]);

function parseHttpsUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return url;
  } catch {
    return null;
  }
}

function isWorkspaceUrl(value) {
  const url = parseHttpsUrl(value);
  return Boolean(url && WEB_ORIGINS.has(url.origin));
}

function isGrokUrl(value) {
  const url = parseHttpsUrl(value);
  return Boolean(url && (url.origin === "https://grok.com" || url.origin === "https://www.grok.com"));
}

function webStateForUrl(value) {
  if (isGrokUrl(value)) return "ready";
  if (isWorkspaceUrl(value)) return "signing-in";
  return "error";
}

module.exports = { WEB_ORIGINS, isWorkspaceUrl, isGrokUrl, parseHttpsUrl, webStateForUrl };
