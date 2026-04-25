import { useEffect, useRef } from 'react';
import type { Turn } from '../types';
import { Message } from './Message';
import { Input } from './Input';

interface Props {
  turns: Turn[];
  loading: boolean;
  onSend: (m: string) => void;
}

export function ChatPane({ turns, loading, onSend }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns]);

  return (
    <div className="flex flex-col flex-1 min-w-0">
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-2">
        {turns.length === 0 && !loading && (
          <div className="h-full flex items-center justify-center text-op-text-muted font-mono text-xs">
            ask echo something
          </div>
        )}
        {turns.map((t) => (
          <Message key={t.turn_index} turn={t} />
        ))}
        {loading && (
          <div className="my-3">
            <div className="text-op-accent text-2xs font-mono mb-1">echo</div>
            <div className="text-op-text-dim text-sm font-mono">
              thinking<span className="inline-block animate-pulse">...</span>
            </div>
          </div>
        )}
      </div>
      <Input onSend={onSend} disabled={loading} />
    </div>
  );
}
