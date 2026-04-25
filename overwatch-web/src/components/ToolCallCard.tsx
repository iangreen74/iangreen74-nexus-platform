import { useState } from 'react';
import type { ToolCall } from '../types';

interface Props { call: ToolCall; }

export function ToolCallCard({ call }: Props) {
  const [expanded, setExpanded] = useState(false);
  const ok = call.outcome.ok;
  const statusColor = ok ? 'text-op-success' : 'text-op-danger';
  const statusGlyph = ok ? 'OK' : 'FAIL';

  return (
    <div className="border border-op-border rounded bg-op-surface my-2 font-mono text-xs">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full px-3 py-2 flex items-center gap-2 hover:bg-op-surface-2 text-left"
      >
        <span className={`${statusColor} font-bold text-2xs`}>{statusGlyph}</span>
        <span className="text-op-accent">{call.tool_name}</span>
        <span className="ml-auto text-op-text-dim text-2xs">
          {expanded ? '[collapse]' : '[expand]'}
        </span>
      </button>
      {!ok && !expanded && call.outcome.error && (
        <div className="px-3 pb-2 text-op-danger text-2xs">
          {call.outcome.error.slice(0, 200)}
        </div>
      )}
      {expanded && (
        <div className="border-t border-op-border px-3 py-2 space-y-2">
          <div>
            <div className="text-op-text-dim text-2xs mb-1">input</div>
            <pre className="text-op-text whitespace-pre-wrap break-all text-2xs">
              {JSON.stringify(call.input, null, 2)}
            </pre>
          </div>
          <div>
            <div className="text-op-text-dim text-2xs mb-1">outcome</div>
            <pre className="text-op-text whitespace-pre-wrap break-all text-2xs">
              {JSON.stringify(call.outcome, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
