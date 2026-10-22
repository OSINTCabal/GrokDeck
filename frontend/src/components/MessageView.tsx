import { Check, ChevronLeft, ChevronRight, Copy, Play, RefreshCw, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { api } from "../lib/api";
import type { Message } from "../types";

function closeFence(text: string) {
  const fences = text.match(/```/g)?.length || 0;
  return fences % 2 === 1 ? `${text}\n\`\`\`` : text;
}

export function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const [html, setHtml] = useState("");
  const [copied, setCopied] = useState(false);
  const [output, setOutput] = useState("");
  const [running, setRunning] = useState(false);
  const runnable = lang === "python" || lang === "py";
  useEffect(() => {
    let cancel = false;
    const language = lang && /^[a-z0-9#+.-]{1,20}$/i.test(lang) ? lang : "text";
    const theme = document.documentElement.classList.contains("dark") ? "github-dark" : "github-light";
    import("shiki")
      .then(({ codeToHtml }) => codeToHtml(code, { lang: language, theme })
        .catch(() => codeToHtml(code, { lang: "text", theme })))
      .then((out) => {
        if (!cancel) setHtml(out);
      })
      .catch(() => {
        if (!cancel) setHtml("");
      });
    return () => {
      cancel = true;
    };
  }, [code, lang]);

  return (
    <div className="codeblock">
      <div className="codeblock-bar">
        <span>{lang || "text"}</span>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard.writeText(code);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
          }}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? "Copied" : "Copy"}
        </button>
        {runnable && (
          <button
            type="button"
            disabled={running}
            onClick={() => {
              setRunning(true);
              void api.runCode(lang || "python", code).then((result) => {
                setOutput(result.output || (result.ok ? "" : "The process failed."));
              }).catch((error: unknown) => {
                setOutput(error instanceof Error ? error.message : "Run failed.");
              }).finally(() => setRunning(false));
            }}
          >
            <Play size={12} /> {running ? "Running" : "Run"}
          </button>
        )}
      </div>
      {output && <pre className="term">{output}</pre>}
      {html ? <div dangerouslySetInnerHTML={{ __html: html }} /> : <pre style={{ margin: 0, padding: 12 }}><code>{code}</code></pre>}
    </div>
  );
}

const markdownComponents: Components = {
  code({ className, children }) {
    const text = String(children).replace(/\n$/, "");
    const lang = /language-([\w#+.-]+)/.exec(className || "")?.[1];
    if (!className) return <code>{text}</code>;
    return <CodeBlock code={text} lang={lang} />;
  },
};

export function MessageView({
  message,
  streaming,
  onEdit,
  onRegenerate,
  onBranch,
  onFeedback,
  onSendToBuild,
}: {
  message: Message;
  streaming: boolean;
  onEdit: (text: string) => void;
  onRegenerate: () => void;
  onBranch: (direction: -1 | 1) => void;
  onFeedback: (value: "up" | "down") => void;
  onSendToBuild?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);
  const user = message.role === "user";
  const siblings = message.sibling_count || 1;
  const showCaret = streaming && message.status === "streaming";

  return (
    <article className={user ? "msg user" : "msg"} data-role={message.role}>
      <div style={{ maxWidth: "100%" }}>
        {user ? (
          editing ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                setEditing(false);
                onEdit(draft);
              }}
            >
              <textarea className="bubble" value={draft} onChange={(event) => setDraft(event.target.value)} aria-label="Edit message" />
              <div className="msg-actions" style={{ opacity: 1 }}>
                <button type="submit">Save</button>
                <button type="button" onClick={() => setEditing(false)}>Cancel</button>
              </div>
            </form>
          ) : (
            <div className="bubble">{message.content}</div>
          )
        ) : (
          <div className="prose">
            {message.reasoning && (
              <details className="reason">
                <summary>Reasoning</summary>
                <div className="reason-body">{message.reasoning}</div>
              </details>
            )}
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[[rehypeKatex, { throwOnError: false }]]}
              components={markdownComponents}
            >
              {closeFence(message.content || (showCaret ? " " : ""))}
            </ReactMarkdown>
            {showCaret && <span className="caret" aria-hidden="true" />}
            {!!message.citations?.length && (
              <div className="sources">
                {message.citations.map((url) => (
                  <a key={url} href={url} target="_blank" rel="noreferrer">{url.replace(/^https?:\/\//, "")}</a>
                ))}
              </div>
            )}
          </div>
        )}
        {!!message.attachments?.length && (
          <div className="files">
            {message.attachments.map((file) => <span key={file.id} className="chip">{file.name}</span>)}
          </div>
        )}
        <div className="msg-actions">
          {user && !editing && <button type="button" onClick={() => { setDraft(message.content); setEditing(true); }}>Edit</button>}
          {user && !editing && onSendToBuild && <button type="button" onClick={onSendToBuild}>Send to Build</button>}
          {!user && <button type="button" onClick={onRegenerate} aria-label="Regenerate"><RefreshCw size={14} /> Regenerate</button>}
          <button
            type="button"
            aria-label="Copy message"
            onClick={() => void navigator.clipboard.writeText(message.content)}
          >
            <Copy size={14} />
          </button>
          {!user && (
            <>
              <button type="button" aria-label="Good response" onClick={() => onFeedback("up")}><ThumbsUp size={14} /></button>
              <button type="button" aria-label="Bad response" onClick={() => onFeedback("down")}><ThumbsDown size={14} /></button>
            </>
          )}
          {siblings > 1 && (
            <span className="branch">
              <button type="button" aria-label="Previous branch" onClick={() => onBranch(-1)}><ChevronLeft size={14} /></button>
              {(message.sibling_index || 0) + 1}/{siblings}
              <button type="button" aria-label="Next branch" onClick={() => onBranch(1)}><ChevronRight size={14} /></button>
            </span>
          )}
        </div>
      </div>
    </article>
  );
}
