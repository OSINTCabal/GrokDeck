#!/usr/bin/env python3
"""Playwright checks for the local GrokDeck UI.

Uses the fake stream. It does not call xAI and it does not start the grok CLI.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
if not PY.exists():
    PY = Path(sys.executable)
SHOTS = Path(os.environ.get("GROKDECK_E2E_SHOTS", ROOT / "docs" / "shots"))


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_health(url: str, proc: subprocess.Popen[bytes]) -> None:
    for _ in range(50):
        if proc.poll() is not None:
            raise SystemExit(f"sidecar exited {proc.returncode}")
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("sidecar did not become healthy")


def check_build_reliability(browser, url: str, home: Path) -> None:
    """Exercise Build races and cancellation with in-browser mock streaming."""
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script(r"""
      window.promptCalls = 0;
      const realFetch = window.fetch;
      window.fetch = (input, init) => {
        if (!String(input).match(/\/api\/build\/sessions\/[^/]+\/prompt$/)) return realFetch(input, init);
        window.promptCalls++;
        const encoder = new TextEncoder();
        return Promise.resolve(new Response(new ReadableStream({start(controller) {
          window.emitBuild = (text) => controller.enqueue(encoder.encode('event: token\ndata: ' + JSON.stringify({text}) + '\n\n'));
          window.finishBuild = () => controller.close();
          init.signal.addEventListener('abort', () => { if (!window.ignoreBuildAbort) controller.error(new DOMException('Stopped', 'AbortError')); });
        }}), {headers: {'Content-Type': 'text/event-stream'}}));
      };
    """)
    sessions = [{"id": name, "title": name, "cwd": str(home), "updatedAt": 1} for name in ("Session one", "Session two")]
    pending_opens = []

    def build_route(route):
        path = route.request.url.split("/api/build/", 1)[1]
        if path == "sessions" and route.request.method == "GET":
            route.fulfill(json={"sessions": sessions, "signedIn": True})
        elif path == "sessions":
            pending_opens.append(route)
        elif path.endswith("/load"):
            index = 0 if "Session%20one" in path else 1
            model = "model-one" if index == 0 else "model-two"
            effort = "low" if index == 0 else "xhigh"
            route.fulfill(json={"session": sessions[index], "model": model, "effort": effort,
                                "models": [{"id": model, "_meta": {"reasoningEfforts": [effort]}}],
                                "messages": [{"id": "history", "role": "assistant", "text": "history\n" * 150,
                                              "verbose": "", "reasoning": "", "tools": []}], "commands": []})
        else:
            route.fulfill(json={"cwd": str(home), "files": [], "diff": ""})

    def settings_route(route):
        response = route.fetch()
        route.fulfill(json={**response.json(), "buildCli": True, "defaultCwd": str(home)})

    page.route("**/api/build/**", build_route)
    page.route("**/api/settings", settings_route)
    page.route("**/api/projects", lambda route: route.fulfill(json=[{"id": "fixture", "name": "Fixture project", "path": str(home), "instructions": ""}]))
    page.goto(url + "/build")
    expect(page.get_by_label("Build prompt")).to_be_enabled()
    page.get_by_label("Build prompt").fill("cancel before open finishes")
    page.get_by_role("button", name="Submit", exact=True).click()
    page.wait_for_function("document.querySelector('button[aria-label=Stop]') !== null")
    expect(page.get_by_role("button", name="New session", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Folder", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Fixture project", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Session one", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Stop", exact=True).click()
    assert len(pending_opens) == 1
    pending_opens.pop().fulfill(json={"id": "cancelled", "title": "Cancelled", "cwd": str(home)})
    expect(page.get_by_role("button", name="New session", exact=True)).to_be_enabled()
    assert page.evaluate("window.promptCalls") == 0
    expect(page.get_by_role("heading", name="What should we build?")).to_be_visible()
    expect(page.get_by_text("cancel before open finishes", exact=True)).to_have_count(0)

    # Remount while open is pending: the shared lock survives until it settles.
    page.get_by_label("Build prompt").fill("cancel by navigating away")
    page.get_by_role("button", name="Submit", exact=True).click()
    page.get_by_role("link", name="Imagine", exact=True).click()
    page.get_by_role("link", name="Build", exact=True).click()
    expect(page.get_by_role("button", name="New session", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Stop", exact=True).click()
    assert len(pending_opens) == 1
    pending_opens.pop().fulfill(json={"id": "unmounted", "title": "Unmounted", "cwd": str(home)})
    expect(page.get_by_role("button", name="New session", exact=True)).to_be_enabled()
    assert page.evaluate("window.promptCalls") == 0

    page.get_by_role("button", name="Session one", exact=True).click()
    expect(page.get_by_role("button", name="Model: model-one", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Thinking: low", exact=True)).to_be_visible()
    page.get_by_role("button", name="Session two", exact=True).click()
    expect(page.get_by_role("button", name="Model: model-two", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Thinking: xhigh", exact=True)).to_be_visible()
    page.get_by_label("Build prompt").fill("stream while reading history")
    page.get_by_role("button", name="Submit", exact=True).click()
    page.wait_for_function("window.promptCalls === 1")
    page.evaluate("text => window.emitBuild(text)", "first chunk\n")
    expect(page.get_by_text("first chunk", exact=True)).to_be_visible()
    log = page.get_by_role("log", name="Verbose terminal")
    log.evaluate("node => {node.scrollTop = 0; node.dispatchEvent(new Event('scroll'));}")
    page.evaluate("text => window.emitBuild(text)", "second chunk\n")
    expect(page.locator(".build-turn").last).to_contain_text("second chunk")
    assert log.evaluate("node => node.scrollTop") == 0
    log.evaluate("node => {node.scrollTop = node.scrollHeight; node.dispatchEvent(new Event('scroll'));}")
    page.evaluate("text => window.emitBuild(text)", "third chunk\n" * 100)
    expect(page.locator(".build-turn").last).to_contain_text("third chunk")
    page.wait_for_function("(() => { const n = document.querySelector('.build-verbose'); return n.scrollHeight - n.scrollTop - n.clientHeight < 5; })()")
    page.evaluate("window.finishBuild()")
    expect(page.get_by_role("button", name="New session", exact=True)).to_be_enabled()
    page.evaluate("window.ignoreBuildAbort = true")
    page.get_by_label("Build prompt").fill("cancel a streaming turn")
    page.get_by_role("button", name="Submit", exact=True).click()
    page.wait_for_function("window.promptCalls === 2")
    page.get_by_role("button", name="Stop", exact=True).click()
    page.evaluate("window.emitBuild('obsolete output'); window.finishBuild()")
    expect(page.get_by_role("button", name="New session", exact=True)).to_be_enabled()
    expect(page.get_by_text("obsolete output", exact=True)).to_have_count(0)
    page.close()


def main() -> None:
    from playwright.sync_api import expect, sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="grokdeck-e2e-"))
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["GROKDECK_HOME"] = str(home)
    env["GROKDECK_GROK_HOME"] = str(home / "grok")
    env["GROKDECK_GROK_BIN"] = str(home / "missing-grok")
    env["GROKDECK_FAKE_STREAM"] = "1"
    env["GROKDECK_FAKE_DELAY"] = "0.35"
    env["GROKDECK_TESTING"] = "1"
    env["GROKDECK_SECRET_PATH"] = str(home / "fixture-secret")
    env.pop("XAI_API_KEY", None)
    env["PYTHONPATH"] = str(ROOT)
    log_path = Path("/tmp/grokdeck-e2e-server.log")
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(PY), "-m", "uvicorn", "sidecar.grokdeck_server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    failures: list[str] = []
    try:
        wait_health(url, proc)
        request = urllib.request.Request(
            url + "/api/providers",
            data=json.dumps({"id": "mistral", "models": ["mistral-large-4"], "key": "fixture-only"}).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=5):
            pass
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda err: print(f"PAGEERROR {err}"))
            page.goto(url)
            expect(page.get_by_role("heading", name="What should we explore?")).to_be_visible()
            page.set_viewport_size({"width": 800, "height": 600})
            expect(page.get_by_role("textbox", name="Ask GrokDeck anything")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.screenshot(path=str(SHOTS / "home-1440x900-light.png"))
            page.set_viewport_size({"width": 1280, "height": 800})
            page.screenshot(path=str(SHOTS / "home-1280x800-light.png"))
            page.set_viewport_size({"width": 1440, "height": 900})

            page.get_by_role("button", name="Open sidebar").click()
            page.get_by_role("button", name="Account").click()
            page.get_by_role("menuitem", name="Settings").click()
            page.get_by_role("button", name="Appearance").click()
            page.get_by_role("radio", name="Dark").click()
            page.keyboard.press("Escape")
            expect(page.locator("html")).to_have_class("dark")
            page.screenshot(path=str(SHOTS / "home-1440x900-dark.png"))
            page.set_viewport_size({"width": 1280, "height": 800})
            page.screenshot(path=str(SHOTS / "home-1280x800-dark.png"))
            page.set_viewport_size({"width": 1440, "height": 900})
            page.get_by_role("button", name="Account").click()
            page.get_by_role("menuitem", name="Settings").click()
            page.get_by_role("button", name="Appearance").click()
            page.get_by_role("radio", name="Light").click()
            for label in ("Account", "Providers", "Appearance", "Behavior", "Customize", "Data Controls", "Usage", "Billing"):
                page.get_by_role("navigation", name="Settings").get_by_role("button", name=label, exact=True).click()
                expect(page.get_by_role("heading", name=label, exact=True)).to_be_visible()
            page.keyboard.press("Escape")

            box = page.get_by_role("textbox", name="Ask GrokDeck anything")
            box.fill("Use DeepSeek subagents to review this code")
            page.get_by_role("button", name="Submit").click()
            expect(page.get_by_role("alert")).to_contain_text("Connect DeepSeek API in Settings")
            expect(page.get_by_role("heading", name="What should we explore?")).to_be_visible()
            box.fill("zebra-bridge-e2e")
            page.get_by_role("button", name="Submit").click()
            expect(page.get_by_text("The sum is")).to_be_visible(timeout=15000)
            expect(page.get_by_role("link", name="example.com/local-preview")).to_be_visible()
            expect(page.get_by_role("button", name="Stop")).to_be_hidden(timeout=15000)
            page.wait_for_timeout(600)
            page.get_by_role("button", name="Run").first.click()
            expect(page.locator("pre.term").first).to_contain_text("2", timeout=10000)

            box.fill("stop-this-turn")
            page.get_by_role("button", name="Submit").click()
            page.get_by_role("button", name="Stop").click()
            expect(page.get_by_role("button", name="Stop")).to_be_hidden(timeout=10000)
            page.wait_for_timeout(500)
            if page.get_by_text("The sum is").count() != 1:
                failures.append(f"stop did not keep a single finished answer (count={page.get_by_text('The sum is').count()})")

            finished = page.locator("article").filter(has_text="The sum is")
            finished.hover()
            finished.get_by_role("button", name="Regenerate").click()
            expect(page.get_by_text("2/2")).to_be_visible(timeout=15000)
            expect(page.get_by_role("button", name="Stop")).to_be_hidden(timeout=15000)
            page.wait_for_timeout(400)

            original = page.locator("article").filter(has_text="zebra-bridge-e2e")
            original.hover()
            original.get_by_role("button", name="Edit", exact=True).click()
            page.get_by_role("textbox", name="Edit message").fill("zebra-bridge-edited")
            page.get_by_role("button", name="Save").click()
            expect(page.get_by_text("zebra-bridge-edited")).to_be_visible(timeout=15000)
            edited = page.locator("article").filter(has_text="zebra-bridge-edited")
            edited.hover()
            edited.get_by_role("button", name="Send to Build").click()
            expect(page.get_by_label("Build prompt")).to_have_value("zebra-bridge-edited")
            page.goto(url + "/local")

            page.get_by_role("button", name="New chat").click()
            expect(page.get_by_role("heading", name="What should we explore?")).to_be_visible()
            page.wait_for_timeout(300)
            note = home / "note.txt"
            note.write_text("attachment-marker\n", encoding="utf-8")
            page.locator("input[type=file]").last.set_input_files(str(note))
            expect(page.get_by_text("note.txt").first).to_be_visible()
            page.get_by_role("textbox", name="Ask GrokDeck anything").fill("see the file")
            page.get_by_role("button", name="Submit").click()
            expect(page.get_by_text("The sum is").last).to_be_visible(timeout=15000)
            expect(page.get_by_text("note.txt").first).to_be_visible()

            page.keyboard.press("Control+k")
            page.get_by_role("textbox", name="Search chats").fill("zebra-bridge-edited")
            expect(page.get_by_text("zebra-bridge-edited").last).to_be_visible(timeout=10000)
            page.keyboard.press("Escape")

            page.get_by_role("button", name="Account").click()
            page.get_by_role("menuitem", name="Settings").click()
            page.get_by_role("button", name="Account").last.click()
            expect(page.get_by_label("API key")).to_be_visible()
            page.keyboard.press("Escape")

            page.get_by_role("button", name="Thinking: Fast").click()
            page.get_by_role("menuitem", name="Build", exact=True).click()
            expect(page.get_by_role("heading", name="What should we build?")).to_be_visible()
            expect(page.get_by_label("Build prompt")).to_be_disabled()
            expect(page.get_by_role("button", name="Build mode: Ask")).to_be_visible()
            expect(page.get_by_role("button", name="Thinking: high")).to_be_visible()
            expect(page.locator(".stage-swap")).to_have_count(1)
            page.screenshot(path=str(SHOTS / "build-1440x900-light.png"))

            page.goto(url + "/imagine")
            expect(page.get_by_role("heading", name="What should we imagine?")).to_be_visible()
            page.get_by_role("textbox", name="Type to imagine").fill("a paper study")
            page.get_by_role("button", name="Submit").click()
            expect(page.get_by_text("not called in the local preview")).to_be_visible(timeout=10000)
            expect(page.get_by_role("button", name="Attach")).to_be_disabled()
            expect(page.get_by_role("button", name="Wide frame")).to_be_disabled()
            expect(page.get_by_role("button", name="Tall frame")).to_be_disabled()
            page.screenshot(path=str(SHOTS / "imagine-1440x900-light.png"))

            page.goto(url + "/library")
            expect(page.get_by_role("tab", name="All")).to_be_visible()
            page.get_by_role("button", name="Sort: newest first").click()
            expect(page.get_by_role("button", name="Sort: oldest first")).to_be_visible()
            page.locator("input[type=file]").set_input_files(str(note))
            expect(page.get_by_text("note.txt").first).to_be_visible()
            page.reload()
            expect(page.get_by_text("note.txt").first).to_be_visible()

            page.goto(url + "/automations")
            page.get_by_role("button", name="New Automation").click()
            page.get_by_role("textbox", name="Automation name").fill("Sweep draft")
            page.get_by_role("dialog", name="New automation").get_by_role("button", name="Add").click()
            expect(page.get_by_text("Sweep draft")).to_be_visible()
            page.reload()
            page.get_by_role("tab", name="Runs").click()
            expect(page.get_by_text("Sweep draft")).to_be_visible()

            desktop = browser.new_page(viewport={"width": 1440, "height": 900})
            desktop.add_init_script("""window.webCalls = {open: 0, close: 0, newChat: 0, signOut: 0, refresh: 0, listener: null}; window.grokdeck = {
              grokWeb: { open: async () => { webCalls.open++; return true; }, resize: async () => true,
                close: async () => { webCalls.close++; return true; },
                newChat: async () => { webCalls.newChat++; return true; },
                signOut: async () => { webCalls.signOut++; return true; },
                refresh: async () => { webCalls.refresh++; return true; },
                onState: (listener) => { webCalls.listener = listener; return () => {}; } }
            }""")
            desktop.goto(url)
            expect(desktop.get_by_role("navigation", name="Primary").get_by_role("link", name="Grok Chat")).to_be_visible()
            expect(desktop.locator(".sidebar-slot")).to_have_css("width", "260px")
            assert desktop.evaluate("document.querySelector('.web-chat').getBoundingClientRect().left >= 260")
            desktop.get_by_role("button", name="New chat").click()
            assert desktop.evaluate("webCalls.newChat") == 1
            desktop.get_by_role("button", name="Account").click()
            desktop.get_by_role("menuitem", name="Settings").click()
            expect(desktop.get_by_role("dialog")).to_be_visible()
            assert desktop.evaluate("webCalls.close") > 0
            desktop.keyboard.press("Escape")
            desktop.get_by_role("button", name="Account").click()
            desktop.evaluate("webCalls.listener({status: 'signing-in', detail: 'Finish signing in to Grok Chat in this window.'})")
            expect(desktop.get_by_text("Grok Chat: finish sign-in in the chat window.")).to_be_visible()
            desktop.keyboard.press("Escape")
            desktop.get_by_role("button", name="Account").click()
            desktop.get_by_role("menuitem", name="Switch Grok Chat account").click()
            desktop.wait_for_function("webCalls.signOut === 1 && webCalls.open > 1")
            opened = desktop.evaluate("webCalls.open")
            desktop.evaluate("webCalls.listener({status: 'error', detail: 'Grok could not open.'})")
            desktop.get_by_role("button", name="Reload").click()
            desktop.wait_for_function(f"webCalls.open === {opened + 1}")
            assert desktop.evaluate("webCalls.refresh") == 0
            desktop.get_by_label("Chat model").select_option("mistral:mistral-large-4")
            expect(desktop.get_by_role("heading", name="What should we explore?")).to_be_visible()
            expect(desktop.get_by_label("Chat model")).to_have_value("mistral:mistral-large-4")
            desktop.get_by_role("button", name="Close sidebar").click()
            expect(desktop.get_by_role("button", name="Open sidebar")).to_be_visible()
            desktop.get_by_role("button", name="Open sidebar").click()
            expect(desktop.get_by_role("button", name="Close sidebar")).to_be_visible()
            desktop.close()
            check_build_reliability(browser, url, home)
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
    if failures:
        print("\n".join(failures))
        sys.exit(1)
    print(f"e2e ok  {url}  shots={SHOTS}")


if __name__ == "__main__":
    main()
