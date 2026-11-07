import assert from "node:assert/strict";
import test from "node:test";
import { asksForWorkers } from "../src/lib/workerRequest.ts";

test("delegation requires a request, not a mention", () => {
  for (const prompt of [
    "How do AI agents differ from humans?",
    "Explain how to use agents in software development.",
    "Use cases for AI agents in software development.",
    "Do not use agents for this review.",
    "Please review this without subagents.",
  ]) {
    assert.equal(asksForWorkers(prompt, false), false, prompt);
    assert.equal(asksForWorkers(prompt, true), false, prompt);
  }
});

test("direct delegation requests still run", () => {
  for (const prompt of [
    "Use DeepSeek and Z.ai agents with high thinking.",
    "Send off subagents to review this.",
    "Can you use workers for this task?",
    "Please ask an agent to check the code.",
  ]) assert.equal(asksForWorkers(prompt, false), true, prompt);
});

test("automatic policy keeps long-task routing unless declined", () => {
  const task = `Review this implementation and its failure modes. ${"detail ".repeat(90)}`;
  assert.equal(asksForWorkers(task, false), false);
  assert.equal(asksForWorkers(task, true), true);
  assert.equal(asksForWorkers(`${task} Do not use agents.`, true), false);
});
