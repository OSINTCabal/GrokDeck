import { Image as ImageIcon, Mic, Plus, RectangleHorizontal, RectangleVertical } from "lucide-react";
import { useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../lib/api";
import { useDeck } from "../state";
import { useVoiceCapture } from "../lib/voice";
import { useTitleSlot } from "./TitleBar";
import { MarkButton } from "./TopActions";

const TILES = [
  { name: "Text Edit", kind: "text", src: "/imagine/text.jpg" },
  { name: "Precise Edit", kind: "precise", src: "/imagine/precise.jpg" },
  { name: "Loop", kind: "loop", src: "/imagine/loop.jpg" },
  { name: "Photo Edit", kind: "photo", src: "/imagine/photo.jpg" },
  { name: "Reimagine", kind: "reimagine", src: "/imagine/reimagine.jpg" },
];

export function Imagine() {
  const sidebarOpen = useDeck((s) => s.sidebarOpen);
  const titleSlot = useTitleSlot();
  const [prompt, setPrompt] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [canvas, setCanvas] = useState(0);
  const voice = useVoiceCapture((heard) => {
    setPrompt((current) => (current.trim() ? `${current.trim()} ${heard}` : heard));
  });

  async function submit() {
    const value = prompt.trim();
    if (!value || busy) return;
    setBusy(true);
    setNote("");
    try {
      const result = await api.imagine(value);
      setNote(result.note || (result.image ? "Image ready." : "No image was returned."));
    } catch (error) {
      setNote(error instanceof Error ? error.message : "Imagine did not run.");
    } finally {
      setBusy(false);
    }
  }

  const freshCanvas = (
    <button
      type="button"
      className="pill ghost"
      onClick={() => {
        setCanvas((value) => value + 1);
        setPrompt("");
        setNote("");
      }}
    >
      <Plus size={14} /> New canvas
    </button>
  );

  return (
    <div className="stage imagine">
      {titleSlot ? (
        createPortal(freshCanvas, titleSlot)
      ) : (
        <div className="top">
          <div>{!sidebarOpen && <MarkButton />}</div>
          {freshCanvas}
        </div>
      )}
      <h1 className="home-title">What should we imagine?</h1>
      <form
        key={canvas}
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <textarea
          aria-label="Type to imagine"
          placeholder="Type to imagine"
          value={prompt}
          rows={1}
          onChange={(event) => setPrompt(event.target.value)}
        />
        <div className="composer-bar">
          <button type="button" className="icon-btn" aria-label="Attach" title="Image editing is not available yet" disabled>
            <Plus size={18} />
          </button>
          <span className="chip on"><ImageIcon size={14} /> Image</span>
          <button type="button" className="icon-btn" aria-label="Wide frame" title="Frame options are not available yet" disabled><RectangleHorizontal size={16} /></button>
          <button type="button" className="icon-btn" aria-label="Tall frame" title="Frame options are not available yet" disabled><RectangleVertical size={16} /></button>
          <span className="chip">Speed</span>
          <span className="chip">Quality 2.0</span>
          <span style={{ flex: 1 }} />
          <span className="chip quiet">x4</span>
          <span className="chip quiet">2:3</span>
          {prompt.trim() ? (
            <button type="submit" className="send" aria-label="Submit" disabled={busy}>↑</button>
          ) : (
            <button
              type="button"
              className="icon-btn"
              aria-label="Microphone"
              aria-pressed={voice.listening}
              onClick={() => void voice.toggle()}
            >
              <Mic size={16} />
            </button>
          )}
        </div>
      </form>
      {(voice.listening || voice.notice || note) && (
        <p className="banner" role="status">
          {voice.listening ? "Listening. Tap again to finish." : voice.notice || note}
        </p>
      )}
      <div className="imagine-grid">
        {TILES.map((tile) => (
          <button
            key={tile.name}
            type="button"
            className={`tile ${tile.kind}`}
            style={{ backgroundImage: `url(${tile.src})` }}
            onClick={() => setPrompt(tile.name)}
          >
            {tile.kind === "text" && <span className="tile-type">COLD BREW<i /></span>}
            {tile.kind === "precise" && <span className="select-box" />}
            {tile.kind === "loop" && (
              <span className="film">
                {["left", "center", "right", "bottom"].map((spot) => (
                  <i key={spot} style={{ backgroundImage: `url(${tile.src})`, backgroundPosition: spot }} />
                ))}
              </span>
            )}
            <span className="caption">{tile.name}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
