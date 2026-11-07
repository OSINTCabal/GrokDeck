function localFromProtocol(raw, base) {
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== "grokdeck:") return null;
    const route = `${parsed.hostname}${parsed.pathname}`.replace(/^\/+|\/+$/g, "");
    return `${String(base).replace(/\/$/, "")}/${route}`;
  } catch {
    return null;
  }
}

module.exports = { localFromProtocol };
