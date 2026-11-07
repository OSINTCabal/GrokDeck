import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ArrowUp, Boxes, ChevronDown, Lightbulb, Mic, Plus, Rocket, Square, Wrench, X, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useBuild } from "../lib/buildState";
import { speakable, useVoiceCapture } from "../lib/voice";
import { useDeck } from "../state";
import type { ModeId } from "../types";

const MODES: { id: ModeId; label: string; icon: typeof Zap }[] = [
  { id: "auto", label: "Auto", icon: Rocket },
  { id: "fast", label: "Fast", icon: Zap },
  { id: "expert", label: "Expert", icon: Lightbulb },
  { id: "heavy", label: "Heavy", icon: Boxes },
];

function Waveform() {
  return (
    <span className="wave" aria-hidden="true">
      <i /><i /><i /><i /><i /><i /><i />
    </span>
  );
}

export function Composer({ placeholder = "Type / to use slash commands" }: { placeholder?: string }) {
  const [text, setText] = useState("");
  const [drag, setDrag] = useState(false);
  const [menu, setMenu] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const intent = useRef<"dictate" | "talk">("dictate");
  const playback = useRef<(blob: Blob) => Promise<void>>(async () => undefined);
  const report = useRef<(message: string) => void>(() => undefined);
  const fileRef = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const navigate = useNavigate();
  const mode = useDeck((s) => s.mode);
  const setMode = useDeck((s) => s.setMode);
  const send = useDeck((s) => s.send);
  const stop = useDeck((s) => s.stop);
  const streaming = useDeck((s) => s.streaming);
  const pending = useDeck((s) => s.pending);
  const addFiles = useDeck((s) => s.addFiles);
  const removePending = useDeck((s) => s.removePending);
  const searchOn = useDeck((s) => s.searchOn);
  const setSearchOn = useDeck((s) => s.setSearchOn);
  const voice = useVoiceCapture(async (heard) => {
    if (intent.current === "dictate") {
      setText((current) => (current.trim() ? `${current.trim()} ${heard}` : heard));
      return;
    }
    setSpeaking(true);
    report.current("");
    try {
      await send(heard);
      const reply = [...useDeck.getState().messages].reverse().find((item) => item.role === "assistant" && item.content);
      const line = speakable(reply?.content || "");
      if (!line) return;
      await playback.current(await api.speak(line));
    } catch (error) {
      report.current(error instanceof Error ? error.message : "Voice did not finish.");
    } finally {
      setSpeaking(false);
    }
  });
  playback.current = (blob) => voice.play(blob);
  report.current = voice.setNotice;
  const reasoningLabel = MODES.find((item) => item.id === mode)?.label || "Fast";
  const label = `Thinking: ${reasoningLabel}`;
  const slash = text.startsWith("/") && !text.includes("\n");
  const query = text.slice(1).toLowerCase();
  const slashModes = MODES.filter((item) => item.label.toLowerCase().includes(query));

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  function submit() {
    if (streaming) {
      void stop();
      return;
    }
    const value = text.trim();
    if (!value) return;
    setText("");
    voice.setNotice("");
    void send(value);
  }

  return (
    <div className="composer-wrap">
      {slash && (
        <div className="slash" role="listbox" aria-label="Modes">
          <div className="slash-label">Modes</div>
          {slashModes.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className="menu-item"
                onClick={() => {
                  setMode(item.id);
                  setText("");
                }}
              >
                <Icon size={14} /> {item.label}
              </button>
            );
          })}
          {"build".includes(query) && (
            <button type="button" className="menu-item" onClick={() => { setText(""); navigate("/build"); }}>
              <Wrench size={14} /> Build
            </button>
          )}
          {"search".includes(query) && (
            <button type="button" className="menu-item" onClick={() => { setSearchOn(!searchOn); setText(""); }}>
              Search {searchOn ? "on" : "off"}
            </button>
          )}
          {"voice".includes(query) && (
            <button
              type="button"
              className="menu-item"
              onClick={() => {
                setText("");
                intent.current = "talk";
                void voice.toggle();
              }}
            >
              <Mic size={14} /> Voice
            </button>
          )}
        </div>
      )}
      {pending.length > 0 && (
        <div className="files" style={{ marginBottom: 8 }}>
          {pending.map((file) => (
            <span key={file.id} className="chip">
              {file.name}
              <button type="button" aria-label={`Remove ${file.name}`} onClick={() => removePending(file.id)}>
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      <form
        className={drag ? "composer drag" : "composer"}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDrag(false);
          void addFiles([...event.dataTransfer.files]);
        }}
      >
        <textarea
          ref={box}
          aria-label="Ask GrokDeck anything"
          placeholder={placeholder}
          value={text}
          rows={1}
          onChange={(event) => setText(event.target.value)}
          onPaste={(event) => {
            const files = [...event.clipboardData.files];
            if (files.length) {
              event.preventDefault();
              void addFiles(files);
            }
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
            if (event.key === "Escape") setMenu(false);
          }}
        />
        <div className="composer-bar">
          <button type="button" className="icon-btn" aria-label="Attach" onClick={() => fileRef.current?.click()}>
            <Plus size={18} />
          </button>
          <input
            ref={fileRef}
            type="file"
            hidden
            multiple
            onChange={(event) => {
              void addFiles([...(event.target.files || [])]);
              event.target.value = "";
            }}
          />
          <span style={{ flex: 1 }} />
          <DropdownMenu.Root open={menu} onOpenChange={setMenu}>
            <DropdownMenu.Trigger className="model-btn" aria-label={label}>
              {label}
              <ChevronDown size={14} />
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content className="menu model-menu" side="bottom" align="end" sideOffset={8}>
                <DropdownMenu.RadioGroup value={mode} onValueChange={(value) => setMode(value as ModeId)}>
                  {MODES.filter((item) => item.id !== "heavy").map((item) => {
                    const Icon = item.icon;
                    return (
                      <DropdownMenu.RadioItem key={item.id} className="menu-item" value={item.id}>
                        <Icon size={16} />
                        <span>{item.label}</span>
                        <DropdownMenu.ItemIndicator className="menu-check">✓</DropdownMenu.ItemIndicator>
                      </DropdownMenu.RadioItem>
                    );
                  })}
                </DropdownMenu.RadioGroup>
                <DropdownMenu.Item className="menu-item" onSelect={() => {
                  useBuild.setState({ handoffPrompt: text.trim() });
                  navigate("/build");
                }}>
                  <Wrench size={16} /> Build
                </DropdownMenu.Item>
                <DropdownMenu.RadioGroup value={mode} onValueChange={(value) => setMode(value as ModeId)}>
                  {MODES.filter((item) => item.id === "heavy").map((item) => {
                    const Icon = item.icon;
                    return (
                      <DropdownMenu.RadioItem key={item.id} className="menu-item" value={item.id}>
                        <Icon size={16} />
                        <span>{item.label}</span>
                        <DropdownMenu.ItemIndicator className="menu-check">✓</DropdownMenu.ItemIndicator>
                      </DropdownMenu.RadioItem>
                    );
                  })}
                </DropdownMenu.RadioGroup>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
          {text.trim() || streaming ? (
            <button
              type={streaming ? "button" : "submit"}
              className="send"
              aria-label={streaming ? "Stop" : "Submit"}
              onClick={streaming ? () => { voice.stopPlayback(); void stop(); } : undefined}
            >
              {streaming ? <Square size={14} fill="currentColor" /> : <ArrowUp size={18} />}
            </button>
          ) : (
            <>
              <button
                type="button"
                className="icon-btn"
                aria-label="Microphone"
                aria-pressed={voice.listening && intent.current === "dictate"}
                onClick={() => {
                  intent.current = "dictate";
                  void voice.toggle();
                }}
              >
                <Mic size={16} />
              </button>
              <button
                type="button"
                className="voice"
                aria-label="Voice"
                aria-pressed={(voice.listening && intent.current === "talk") || speaking}
                onClick={() => {
                  intent.current = "talk";
                  void voice.toggle();
                }}
              >
                <Waveform />
              </button>
            </>
          )}
        </div>
      </form>
      {(voice.listening || speaking || voice.notice) && (
        <p className="banner" role="status">
          {voice.listening ? "Listening. Tap again to finish." : speaking ? "Speaking." : voice.notice}
        </p>
      )}
    </div>
  );
}
