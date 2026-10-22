import { useEffect, useRef, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { trackPointer } from "../lib/drag";
import { useDeck } from "../state";
import { Sidebar } from "./Sidebar";
import { TitleBar, TitleSlotProvider } from "./TitleBar";

const EDGES = ["n", "s", "e", "w", "ne", "nw", "se", "sw"] as const;

export function Shell({ children }: { children: ReactNode }) {
  const sidebarOpen = useDeck((s) => s.sidebarOpen);
  const slot = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = slot.current;
    if (!node) return;
    if (sidebarOpen) node.removeAttribute("inert");
    else node.setAttribute("inert", "");
  }, [sidebarOpen]);

  useEffect(() => {
    if (!window.grokdeck) return;
    document.documentElement.classList.add("desk");
    return () => document.documentElement.classList.remove("desk");
  }, []);

  return (
    <TitleSlotProvider>
      <div className="app">
        <a className="skip" href="#main">Skip to main content</a>
        <ResizeEdges />
        <TitleBar />
        <div className="workspace">
          <div ref={slot} className={sidebarOpen ? "sidebar-slot" : "sidebar-slot closed"}>
            <Sidebar />
          </div>
          <div id="main" className="main">
            {children}
          </div>
        </div>
      </div>
    </TitleSlotProvider>
  );
}

function ResizeEdges() {
  if (!window.grokdeck?.resizeBy) return null;
  return (
    <>
      {EDGES.map((edge) => (
        <div
          key={edge}
          className={`resize-grip ${edge}`}
          onPointerDown={(event) => beginResize(edge, event)}
        />
      ))}
    </>
  );
}

function beginResize(edge: (typeof EDGES)[number], event: ReactPointerEvent<HTMLDivElement>) {
  if (event.button !== 0 || !window.grokdeck?.resizeStart || !window.grokdeck.resizeBy) return;
  document.documentElement.classList.add("resizing");
  void window.grokdeck.resizeStart();
  trackPointer(
    event,
    (dx, dy) => window.grokdeck?.resizeBy?.(edge, dx, dy),
    () => {
      document.documentElement.classList.remove("resizing");
      void window.grokdeck?.resizeEnd?.();
    },
  );
}
