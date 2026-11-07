import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useBuild } from "../lib/buildState";
import { useDeck } from "../state";
import type { Message } from "../types";
import { Composer } from "./Composer";
import { MessageView } from "./MessageView";
import { MarkButton } from "./TopActions";

export function Thread() {
  const navigate = useNavigate();
  const messages = useDeck((s) => s.messages);
  const streaming = useDeck((s) => s.streaming);
  const error = useDeck((s) => s.error);
  const sidebarOpen = useDeck((s) => s.sidebarOpen);
  const edit = useDeck((s) => s.edit);
  const regenerate = useDeck((s) => s.regenerate);
  const branch = useDeck((s) => s.branch);
  const feedback = useDeck((s) => s.feedback);
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 140,
    overscan: 6,
    getItemKey: (index) => messages[index]?.id || index,
  });

  const tail = messages[messages.length - 1]?.content || "";
  useEffect(() => {
    if (!messages.length) return;
    virtualizer.scrollToIndex(messages.length - 1, { align: "end" });
  }, [messages.length, tail]);

  function move(message: Message, direction: -1 | 1) {
    const ids = message.sibling_ids || [];
    const next = ids[(message.sibling_index || 0) + direction];
    if (next) void branch(next);
  }

  return (
    <div className="thread-screen">
      {!window.grokdeck && (
        <div className="top">
          <div>{!sidebarOpen && <MarkButton />}</div>
        </div>
      )}
      <div className="thread-scroll" ref={parentRef}>
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {virtualizer.getVirtualItems().map((item) => {
            const message = messages[item.index];
            if (!message) return null;
            return (
              <div
                key={item.key}
                data-index={item.index}
                ref={virtualizer.measureElement}
                style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${item.start}px)` }}
              >
                <div className="thread-inner">
                  <MessageView
                    message={message}
                    streaming={streaming}
                    onEdit={(text) => void edit(message.id, text)}
                    onRegenerate={() => void regenerate(message.id)}
                    onBranch={(direction) => move(message, direction)}
                    onFeedback={(value) => void feedback(message.id, value)}
                    onSendToBuild={() => {
                      useBuild.setState({ handoffPrompt: message.content });
                      navigate("/build");
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {error && <p className="banner" role="alert">{error}</p>}
      <div className="thread-composer">
        <Composer />
      </div>
    </div>
  );
}
