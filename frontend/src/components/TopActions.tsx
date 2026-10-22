import { useDeck } from "../state";
import { Mark } from "./Mark";

export function MarkButton() {
  const toggleSidebar = useDeck((s) => s.toggleSidebar);
  return (
    <button type="button" className="mark" aria-label="Open sidebar" onClick={toggleSidebar}>
      <Mark size={18} />
    </button>
  );
}
