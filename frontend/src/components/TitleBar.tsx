import { createContext, useContext, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { Minus, Square, X } from "lucide-react";
import { trackPointer } from "../lib/drag";
import { useDeck } from "../state";
import { MarkButton } from "./TopActions";

const TitleSlotContext = createContext<{
  el: HTMLElement | null;
  setEl: (el: HTMLElement | null) => void;
}>({ el: null, setEl: () => {} });

export function TitleSlotProvider({ children }: { children: ReactNode }) {
  const [el, setEl] = useState<HTMLElement | null>(null);
  return <TitleSlotContext.Provider value={{ el, setEl }}>{children}</TitleSlotContext.Provider>;
}

export function useTitleSlot() {
  return useContext(TitleSlotContext).el;
}

function beginMove(event: ReactPointerEvent<HTMLElement>) {
  const desk = window.grokdeck;
  if (event.button !== 0 || !desk?.moveStart || !desk.moveBy) return;
  document.documentElement.classList.add("moving");
  void desk.moveStart();
  trackPointer(
    event,
    (dx, dy) => desk.moveBy?.(dx, dy),
    () => {
      document.documentElement.classList.remove("moving");
      void desk.moveEnd?.();
    },
  );
}

export function TitleBar() {
  const desk = window.grokdeck;
  const sidebarOpen = useDeck((s) => s.sidebarOpen);
  const setSlot = useContext(TitleSlotContext).setEl;
  if (!desk) return null;
  return (
    <header className="titlebar">
      <span className={sidebarOpen ? "title-side" : "title-side closed"} />
      {!sidebarOpen && <MarkButton />}
      <span className="title-drag" onPointerDown={beginMove} />
      <span ref={setSlot} className="title-slot" />
      <span className="win-group">
        <button type="button" className="win-btn" aria-label="Minimize" onClick={() => desk.minimize()}>
          <Minus size={14} />
        </button>
        <button type="button" className="win-btn" aria-label="Maximize" onClick={() => desk.toggleMaximize()}>
          <Square size={12} />
        </button>
        <button type="button" className="win-btn" aria-label="Close" onClick={() => desk.close()}>
          <X size={14} />
        </button>
      </span>
    </header>
  );
}
