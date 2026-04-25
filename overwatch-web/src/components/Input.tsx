import { useState, useRef, KeyboardEvent, ChangeEvent } from 'react';

interface Props { onSend: (m: string) => void; disabled?: boolean; }

export function Input({ onSend, disabled }: Props) {
  const [text, setText] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      submit();
    }
  }

  function submit() {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText('');
    if (ref.current) ref.current.style.height = 'auto';
  }

  function autoResize(e: ChangeEvent<HTMLTextAreaElement>) {
    setText(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = `${Math.min(e.target.scrollHeight, 300)}px`;
  }

  return (
    <div className="border-t border-op-border bg-op-surface px-4 py-3">
      <textarea
        ref={ref}
        value={text}
        onChange={autoResize}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        placeholder="Ask Echo. Cmd+Enter to send."
        rows={2}
        className="w-full bg-op-bg text-op-text border border-op-border rounded px-3 py-2 font-mono text-sm resize-none focus:outline-none focus:border-op-accent disabled:opacity-50"
      />
      <div className="flex justify-end mt-2">
        <button
          type="button"
          onClick={submit}
          disabled={disabled || !text.trim()}
          className="px-3 py-1 bg-op-accent text-op-bg font-mono text-2xs font-semibold rounded hover:bg-op-accent-dim disabled:opacity-50"
        >
          {disabled ? 'thinking...' : 'send (cmd+enter)'}
        </button>
      </div>
    </div>
  );
}
