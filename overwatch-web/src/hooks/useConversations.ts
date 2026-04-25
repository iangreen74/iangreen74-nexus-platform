import { useEffect, useState, useCallback } from 'react';
import { listConversations } from '../api';
import type { ConversationSummary } from '../types';

export function useConversations() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await listConversations();
      setConversations(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'unknown error');
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return { conversations, refresh, error };
}
