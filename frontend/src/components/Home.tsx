import { useDeck } from "../state";
import { Composer } from "./Composer";
import { MarkButton } from "./TopActions";

export function Home() {
  const error = useDeck((s) => s.error);
  const sidebarOpen = useDeck((s) => s.sidebarOpen);
  const privateOn = useDeck((s) => s.privateOn);
  const desk = Boolean(window.grokdeck);
  return (
    <div className="stage home">
      {!desk && (
        <div className="top">
          <div>{!sidebarOpen && <MarkButton />}</div>
        </div>
      )}
      <h1 className="home-title">What should we explore?</h1>
      <Composer />
      {privateOn && <p className="banner" role="status">Private chat. It stays off the sidebar.</p>}
      {error && <p className="banner" role="alert">{error}</p>}
    </div>
  );
}
