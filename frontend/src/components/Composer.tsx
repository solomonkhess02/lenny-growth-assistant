import { useState } from "react";

export default function Composer({
  onSend, busy, disabled,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  disabled: boolean;
}) {
  const [input, setInput] = useState("");

  function submit() {
    const text = input.trim();
    if (!text || busy || disabled) return;
    setInput("");
    onSend(text);
  }

  return (
    <div className="composer">
      <textarea
        value={input}
        placeholder={
          disabled ? "Start a session to ask a question…" : "Ask a product or growth question…"
        }
        disabled={disabled}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        rows={2}
      />
      <button onClick={submit} disabled={busy || disabled || !input.trim()}>
        {busy ? "Streaming…" : "Send"}
      </button>
    </div>
  );
}
