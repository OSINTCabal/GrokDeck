import { RefreshCw } from "lucide-react";
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { useDeck } from "../state";

type WebState = { status: string; detail: string };

function boundsFor(node: HTMLElement): DOMRectInit {
  const rect = node.getBoundingClientRect();
  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
}

export function WebChat() {
  const host = useRef<HTMLDivElement>(null);
  const theme = useDeck((state) => state.theme);
  const settingsOpen = useDeck((state) => state.settingsOpen);
  const searchOpen = useDeck((state) => state.searchOpen);
  const [state, setState] = useState<WebState>({ status: "loading", detail: "Opening Grok…" });
  const bridge = window.grokdeck?.grokWeb;

  const sync = useCallback((open = false) => {
    const node = host.current;
    if (!bridge || !node) return;
    const bounds = boundsFor(node);
    if (open) void bridge.open(bounds, theme);
    else void bridge.resize(bounds);
  }, [bridge, theme]);

  const setWebState = useCallback((next: WebState) => {
    setState(next);
    window.dispatchEvent(new CustomEvent("grokdeck:web-state", { detail: next }));
  }, []);

  useLayoutEffect(() => {
    if (!bridge) return;
    const unsubscribe = bridge.onState(setWebState);
    if (settingsOpen || searchOpen) {
      void bridge.close();
      return unsubscribe;
    }
    sync(true);
    const node = host.current;
    if (!node) return;
    let frame = 0;
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => sync());
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(node);
    window.addEventListener("resize", schedule);
    const reopen = () => sync(true);
    window.addEventListener("grokdeck:web-reset", reopen);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", schedule);
      window.removeEventListener("grokdeck:web-reset", reopen);
      void bridge.close();
      unsubscribe();
    };
  }, [bridge, sync, setWebState, settingsOpen, searchOpen]);

  if (!bridge) return null;
  const needsNotice = state.status !== "ready";
  return (
    <section ref={host} className="web-chat" aria-label="Grok web chat">
      {needsNotice && (
        <div className={state.status === "error" ? "web-chat-state error" : "web-chat-state"} role="status">
          <span>{state.detail || (state.status === "signing-in" ? "Complete sign-in to continue." : "Opening Grok…")}</span>
          {state.status === "error" && (
            <button type="button" onClick={() => sync(true)}>
              <RefreshCw size={14} /> Reload
            </button>
          )}
        </div>
      )}
    </section>
  );
}
