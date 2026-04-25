import ReactMarkdown from 'react-markdown';
import type { Turn } from '../types';
import { ToolCallCard } from './ToolCallCard';

interface Props { turn: Turn; }

export function Message({ turn }: Props) {
  if (turn.role === 'user') {
    return (
      <div className="flex justify-end my-3">
        <div className="max-w-[80%] bg-op-surface-2 text-op-text border border-op-border rounded px-3 py-2 text-sm">
          <div className="text-op-text-muted text-2xs font-mono mb-1">you</div>
          <div className="whitespace-pre-wrap">{turn.content}</div>
        </div>
      </div>
    );
  }

  if (turn.role === 'assistant') {
    return (
      <div className="my-3">
        <div className="text-op-accent text-2xs font-mono mb-1">echo</div>
        <div className="prose prose-invert prose-sm max-w-none text-op-text">
          <ReactMarkdown>{turn.content}</ReactMarkdown>
        </div>
        {turn.tool_calls && turn.tool_calls.length > 0 && (
          <div className="mt-2">
            {turn.tool_calls.map((c, i) => (
              <ToolCallCard key={c.tool_use_id || i} call={c} />
            ))}
          </div>
        )}
      </div>
    );
  }

  // Tool turns are rendered alongside the assistant turn that produced them
  // (via tool_calls). Standalone tool turns are not surfaced in the UI.
  return null;
}
