import type { ConversationSummary } from '../types';

interface Props {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string | null) => void;
  onNew: () => void;
}

export function ConversationList({ conversations, activeId, onSelect, onNew }: Props) {
  return (
    <div className="flex flex-col border-r border-op-border bg-op-surface w-64 flex-shrink-0">
      <button
        type="button"
        onClick={onNew}
        className="m-2 px-3 py-2 border border-op-border rounded font-mono text-xs text-op-text hover:bg-op-surface-2 hover:border-op-accent text-left"
      >
        + new conversation
      </button>
      <div className="flex-1 overflow-y-auto">
        {conversations.length === 0 && (
          <div className="px-3 py-2 text-op-text-muted font-mono text-2xs">
            no prior conversations
          </div>
        )}
        {conversations.map((c) => (
          <button
            key={c.conversation_id}
            type="button"
            onClick={() => onSelect(c.conversation_id)}
            className={`w-full px-3 py-2 text-left border-l-2 hover:bg-op-surface-2 ${
              activeId === c.conversation_id
                ? 'border-l-op-accent bg-op-surface-2'
                : 'border-l-transparent'
            }`}
          >
            <div className="text-op-text text-xs truncate">
              {c.title ?? '(untitled)'}
            </div>
            <div className="text-op-text-muted text-2xs font-mono">
              {c.turn_count} turns
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
