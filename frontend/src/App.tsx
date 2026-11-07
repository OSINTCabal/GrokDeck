import { AnimatePresence, motion } from "framer-motion";
import { useEffect } from "react";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Automations } from "./components/Automations";
import { BuildView } from "./components/BuildView";
import { Home } from "./components/Home";
import { Imagine } from "./components/Imagine";
import { Library } from "./components/Library";
import { SearchDialog } from "./components/SearchDialog";
import { SettingsDialog } from "./components/SettingsDialog";
import { Shell } from "./components/Shell";
import { Thread } from "./components/Thread";
import { WebChat } from "./components/WebChat";
import { applyTheme, useDeck } from "./state";

export function App() {
  const load = useDeck((s) => s.load);
  const activeId = useDeck((s) => s.activeId);
  const accountState = useDeck((s) => s.account?.state);
  const refreshAccount = useDeck((s) => s.refreshAccount);
  const navigate = useNavigate();

  useEffect(() => {
    if (accountState !== "waiting") return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - started > 90_000) {
        window.clearInterval(timer);
        return;
      }
      void refreshAccount();
    }, 1000);
    return () => window.clearInterval(timer);
  }, [accountState, refreshAccount]);

  useEffect(() => {
    void load().catch((error: unknown) => {
      const message = error instanceof Error ? error.message : "The local API is not running.";
      useDeck.setState({ error: message });
    });
  }, [load]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (useDeck.getState().theme === "system") applyTheme("system");
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const deck = useDeck.getState();
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key.toLowerCase() === "b") {
        event.preventDefault();
        deck.toggleSidebar();
      } else if (meta && event.key.toLowerCase() === "k") {
        event.preventDefault();
        deck.setSearchOpen(true);
      } else if (meta && event.key === ",") {
        event.preventDefault();
        deck.setSettingsOpen(true);
      } else if (meta && event.key.toLowerCase() === "n") {
        event.preventDefault();
        deck.newChat();
        navigate("/local");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  const location = useLocation();
  const page = location.pathname === "/local" ? (activeId ? "thread" : "home") : location.pathname;

  return (
    <Shell>
      <AnimatePresence initial={false}>
        <motion.div
          key={page}
          className="stage-swap"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8, pointerEvents: "none" }}
          transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
        >
          <Routes location={location}>
            <Route path="/" element={window.grokdeck?.grokWeb ? <WebChat /> : (activeId ? <Thread /> : <Home />)} />
            <Route path="/local" element={activeId ? <Thread /> : <Home />} />
            <Route path="/imagine" element={<Imagine />} />
            <Route path="/library" element={<Library />} />
            <Route path="/automations" element={<Automations />} />
            <Route path="/build" element={<BuildView />} />
          </Routes>
        </motion.div>
      </AnimatePresence>
      <SettingsDialog />
      <SearchDialog />
    </Shell>
  );
}
