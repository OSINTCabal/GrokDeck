import {
  Atom,
  Banknote,
  Briefcase,
  Calendar,
  ChefHat,
  ClipboardList,
  Cpu,
  Flame,
  Glasses,
  Globe,
  Heart,
  Lightbulb,
  Mail,
  StickyNote,
  Sun,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { useDeck } from "../state";
import { MarkButton } from "./TopActions";

interface Template {
  name: string;
  detail: string;
  hue: string;
  icon: LucideIcon;
}

const BLUE = "#1084fe";
const PINK = "#ff309b";
const ORANGE = "#ff6700";
const PURPLE = "#9159fe";

const GROUPS: { title: string; items: Template[] }[] = [
  {
    title: "News",
    items: [
      { name: "Morning Brief", detail: "Start the day with headlines, market moves, and your weather.", hue: BLUE, icon: Sun },
      { name: "AI News Digest", detail: "Model releases, research, and funding that actually matter.", hue: BLUE, icon: Cpu },
      { name: "X Trends Digest", detail: "The five most interesting conversations on X right now.", hue: BLUE, icon: Flame },
      { name: "Competitor Watch", detail: "Weekly digest of competitor launches, pricing, and hires.", hue: PURPLE, icon: Glasses },
    ],
  },
  {
    title: "Productivity",
    items: [
      { name: "Email Auto-Responder", detail: "Draft concise replies for important email threads each morning.", hue: PINK, icon: Mail },
      { name: "Weekly Review", detail: "Wrap the week: what you did, loose ends, and next priorities.", hue: PINK, icon: ClipboardList },
      { name: "Task Extractor", detail: "Extract action items from recent messages and summarize next steps.", hue: PINK, icon: StickyNote },
      { name: "Daily Planner", detail: "A realistic plan for today with focus blocks and quick wins.", hue: PINK, icon: Calendar },
    ],
  },
  {
    title: "Finance",
    items: [
      { name: "Daily Stock Tracker", detail: "Prices, sentiment, and catalysts for your watchlist every day.", hue: ORANGE, icon: Banknote },
      { name: "Earnings Calendar", detail: "Who reports this week, expectations, and what to watch.", hue: ORANGE, icon: Calendar },
      { name: "Portfolio News Scan", detail: "Only material news for your holdings — earnings, ratings, lawsuits.", hue: ORANGE, icon: Briefcase },
    ],
  },
  {
    title: "Research",
    items: [
      { name: "Competitor Watch", detail: "Weekly digest of competitor launches, pricing, and hires.", hue: PURPLE, icon: Glasses },
      { name: "Paper Watch", detail: "The week’s most interesting research, explained in plain language.", hue: PURPLE, icon: Atom },
      { name: "Weekend Deep Dive", detail: "One briefing memo each week on a topic you’re following.", hue: PURPLE, icon: Lightbulb },
    ],
  },
  {
    title: "Lifestyle",
    items: [
      { name: "Workout Coach", detail: "A daily 45-minute session that rotates through the week.", hue: BLUE, icon: Heart },
      { name: "Meal Planner", detail: "Seven weeknight dinners plus one consolidated grocery list.", hue: BLUE, icon: ChefHat },
      { name: "Language Practice", detail: "A short daily lesson: vocabulary, one grammar point, translation.", hue: BLUE, icon: Globe },
    ],
  },
];

function findTemplate(name: string): Template | undefined {
  for (const group of GROUPS) {
    const hit = group.items.find((item) => item.name === name);
    if (hit) return hit;
  }
  return undefined;
}

function MarkTile({ item }: { item?: Template }) {
  const Icon = item?.icon || Workflow;
  return (
    <span className="auto-mark" style={{ background: item?.hue || "#3a3a3a" }} aria-hidden="true">
      <Icon size={16} strokeWidth={2.25} />
    </span>
  );
}

function readAdded(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem("grokdeck.automations") || "[]");
    return Array.isArray(raw) ? raw.filter((name) => typeof name === "string") : [];
  } catch {
    return [];
  }
}

export function Automations() {
  const sidebarOpen = useDeck((s) => s.sidebarOpen);
  const [tab, setTab] = useState<"Automations" | "Runs">("Automations");
  const [added, setAdded] = useState<string[]>(readAdded);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");

  function add(next: string) {
    const trimmed = next.trim();
    if (!trimmed || added.includes(trimmed)) return;
    const list = [trimmed, ...added];
    localStorage.setItem("grokdeck.automations", JSON.stringify(list));
    setAdded(list);
  }

  return (
    <div className="page-screen">
      <div className="auto-wrap">
        <div className="page-head">
          <div className="page-tabs" role="tablist" aria-label="Automations">
            {!window.grokdeck && !sidebarOpen && <MarkButton />}
            {(["Automations", "Runs"] as const).map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={tab === item}
                className={tab === item ? "page-tab on" : "page-tab"}
                onClick={() => setTab(item)}
              >
                {item}
              </button>
            ))}
          </div>
          <button type="button" className="pill solid" onClick={() => setCreating(true)}>New Automation</button>
        </div>
        {tab === "Runs" ? (
          <div className="auto-list">
            {added.length === 0 && <div className="empty-pane">No automations yet.</div>}
            {added.map((item) => (
              <div key={item} className="auto-run">
                <MarkTile item={findTemplate(item)} />
                <span>{item}</span>
              </div>
            ))}
          </div>
        ) : (
          GROUPS.map((group) => {
            const items = group.items.filter((item) => !added.includes(item.name));
            if (items.length === 0) return null;
            return (
              <section key={group.title} className="auto-group">
                <h2>{group.title}</h2>
                <div className="auto-grid">
                  {items.map((item) => (
                    <article key={`${group.title}-${item.name}`} className="auto-card">
                      <MarkTile item={item} />
                      <div>
                        <b>{item.name}</b>
                        <p>{item.detail}</p>
                      </div>
                      <button type="button" className="auto-add" onClick={() => add(item.name)}>Add</button>
                    </article>
                  ))}
                </div>
              </section>
            );
          })
        )}
      </div>
      {creating && (
        <div className="dialog-overlay" onClick={() => setCreating(false)}>
          <form
            className="dialog"
            role="dialog"
            aria-label="New automation"
            onClick={(event) => event.stopPropagation()}
            onSubmit={(event) => {
              event.preventDefault();
              add(name);
              setName("");
              setCreating(false);
              setTab("Runs");
            }}
          >
            <h2>New automation</h2>
            <label className="field">
              Name
              <input aria-label="Automation name" value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            <p className="note">Saved on this machine. Adding one does not start a Grok turn.</p>
            <div className="row">
              <button type="button" className="pill ghost" onClick={() => setCreating(false)}>Cancel</button>
              <button type="submit" className="pill solid" disabled={!name.trim()}>Add</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
