import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useDeck } from "../state";

interface Hit {
  message_id: string;
  conversation_id: string;
  title: string;
  snippet: string;
}

export function SearchDialog() {
  const open = useDeck((s) => s.searchOpen);
  const setSearchOpen = useDeck((s) => s.setSearchOpen);
  const openConversation = useDeck((s) => s.openConversation);
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);

  useEffect(() => {
    if (!open) return;
    const handle = window.setTimeout(() => {
      void api.search(query).then(setHits).catch(() => setHits([]));
    }, 120);
    return () => window.clearTimeout(handle);
  }, [open, query]);

  return (
    <Dialog.Root open={open} onOpenChange={setSearchOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog" aria-describedby={undefined}>
          <Dialog.Title asChild>
            <h2>Search</h2>
          </Dialog.Title>
          <input
            className="search-input"
            aria-label="Search chats"
            placeholder="Search chats"
            value={query}
            autoFocus
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className="hits" style={{ marginTop: 12 }}>
            {hits.map((hit) => (
              <button
                key={hit.message_id}
                type="button"
                className="hit"
                onClick={() => {
                  void openConversation(hit.conversation_id);
                  setSearchOpen(false);
                  navigate("/local");
                }}
              >
                <b>{hit.title || "New chat"}</b>
                <span>{hit.snippet}</span>
              </button>
            ))}
            {query && hits.length === 0 && <p className="note">No matches.</p>}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
