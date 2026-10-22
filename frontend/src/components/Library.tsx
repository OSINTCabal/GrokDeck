import { Search, SlidersHorizontal, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useDeck } from "../state";
import { MarkButton } from "./TopActions";

const TABS = ["All", "Media", "Projects", "Apps", "Files"] as const;

interface Item {
  id: string;
  name: string;
  size: number;
  mtime: number;
  kind: "Media" | "Files" | "Projects" | "Apps";
  url?: string;
}

const APPS: Item[] = [
  { id: "grok-bot", name: "Grok Bot", size: 0, mtime: 0, kind: "Apps" },
  { id: "grokdeck", name: "GrokDeck", size: 0, mtime: 0, kind: "Apps" },
];

export function Library() {
  const sidebarOpen = useDeck((s) => s.sidebarOpen);
  const [tab, setTab] = useState<(typeof TABS)[number]>("All");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [note, setNote] = useState("");
  const [newestFirst, setNewestFirst] = useState(true);
  const fileRef = useRef<HTMLInputElement>(null);
  const needle = query.trim().toLowerCase();
  const pool = tab === "Apps" ? [...items, ...APPS] : items;
  const visible = pool.filter((item) => {
    const onTab = tab === "All" ? item.kind === "Media" || item.kind === "Files" : tab === item.kind;
    return onTab && item.name.toLowerCase().includes(needle);
  }).sort((a, b) => newestFirst ? b.mtime - a.mtime : a.mtime - b.mtime);

  useEffect(() => {
    let live = true;
    void api.library().then((rows) => {
      if (!live) return;
      setItems((current) => [...rows, ...current.filter((item) => item.kind === "Projects")]);
    }).catch((error: unknown) => {
      if (live) setNote(error instanceof Error ? error.message : "Library did not load.");
    });
    void api.projects().then((projects) => {
      if (!live) return;
      const cards: Item[] = projects.map((project) => ({
        id: project.id,
        name: project.name,
        size: 0,
        mtime: 0,
        kind: "Projects",
      }));
      setItems((current) => [...current.filter((item) => item.kind !== "Projects"), ...cards]);
    }).catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  async function onFiles(list: FileList | null) {
    if (!list?.length) return;
    setNote("");
    const next: Item[] = [];
    for (const file of [...list]) {
      try {
        const saved = await api.upload(file);
        next.push({
          id: saved.id,
          name: saved.name,
          size: file.size,
          mtime: Date.now(),
          kind: file.type.startsWith("image/") ? "Media" : "Files",
          url: `/api/attachments/${saved.id}`,
        });
      } catch (error) {
        setNote(error instanceof Error ? error.message : "Upload did not finish.");
      }
    }
    setItems((current) => [...next, ...current]);
  }

  return (
    <div className="page-screen">
      <div className="page-head">
        <div className="page-tabs" role="tablist" aria-label="Library">
          {!window.grokdeck && !sidebarOpen && <MarkButton />}
          {TABS.map((name) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-selected={tab === name}
              className={tab === name ? "page-tab on" : "page-tab"}
              onClick={() => setTab(name)}
            >
              {name}
            </button>
          ))}
        </div>
        <div className="page-tools">
          <label className="icon-field">
            <Search size={14} />
            <input aria-label="Search library" value={query} placeholder="Search" onChange={(event) => setQuery(event.target.value)} />
          </label>
          <button type="button" className="icon-btn" aria-label={`Sort: ${newestFirst ? "newest first" : "oldest first"}`} onClick={() => setNewestFirst((value) => !value)}>
            <SlidersHorizontal size={16} />
          </button>
          <button type="button" className="pill solid" onClick={() => fileRef.current?.click()}>
            <Upload size={14} /> Upload files
          </button>
          <input
            ref={fileRef}
            type="file"
            hidden
            multiple
            onChange={(event) => {
              void onFiles(event.target.files);
              event.target.value = "";
            }}
          />
        </div>
      </div>
      {note && <p className="banner" role="status">{note}</p>}
      {visible.length === 0 ? (
        <div className="empty-pane">Nothing in {tab === "All" ? "the library" : tab.toLowerCase()} yet.</div>
      ) : (
        <>
          <div className="group-label">Earlier</div>
          <div className="library-grid">
            {visible.map((item) => (
              <article key={item.id} className="library-card">
                <div
                  className="library-thumb"
                  data-kind={item.kind}
                  style={item.url ? { backgroundImage: `url(${item.url})` } : undefined}
                />
                <b>{item.name}</b>
                <span>{item.kind === "Apps" || item.kind === "Projects" ? item.kind : formatMeta(item)}</span>
              </article>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function formatMeta(item: Item) {
  const when = item.mtime
    ? new Date(item.mtime).toLocaleDateString("en-US", { month: "short", day: "numeric" })
    : "";
  const size = formatSize(item.size);
  return when ? `${when} · ${size}` : size;
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
