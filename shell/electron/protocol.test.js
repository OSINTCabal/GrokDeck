const test = require("node:test");
const assert = require("node:assert/strict");
const { localFromProtocol } = require("./protocol");

test("grokdeck links become local routes", () => {
  assert.equal(
    localFromProtocol("grokdeck://imagine", "http://127.0.0.1:9488"),
    "http://127.0.0.1:9488/imagine",
  );
  assert.equal(localFromProtocol("https://example.com", "http://127.0.0.1:9488"), null);
});
