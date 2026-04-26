import { useState } from 'react';
import { OperatorBanner } from './components/OperatorBanner';
import { ConversationList } from './components/ConversationList';
import { ChatPane } from './components/ChatPane';
import { ReportsPanel } from './components/ReportsPanel';
import { useChat } from './hooks/useChat';
import { useConversations } from './hooks/useConversations';
import { getConversation } from './api';
import { flattenTurn } from './types';

export function App() {
  const chat = useChat();
  const { conversations, refresh } = useConversations();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  async function selectConversation(id: string | null) {
    if (id === null) {
      chat.reset();
      setSelectedId(null);
      return;
    }
    try {
      const detail = await getConversation(id);
      const turns = (detail.turns || []).map(flattenTurn);
      chat.loadConversation(id, turns);
      setSelectedId(id);
    } catch {
      // silent fallback - keep current state
    }
  }

  function handleNew() {
    chat.reset();
    setSelectedId(null);
  }

  async function handleSend(message: string) {
    await chat.send(message);
    refresh();
  }

  return (
    <div className="h-screen flex flex-col bg-op-bg">
      <OperatorBanner />
      <div className="flex-1 flex min-h-0">
        <ConversationList
          conversations={conversations}
          activeId={selectedId}
          onSelect={selectConversation}
          onNew={handleNew}
        />
        <ChatPane turns={chat.turns} loading={chat.loading} onSend={handleSend} />
        <ReportsPanel />
      </div>
      {chat.error && (
        <div className="bg-op-danger text-op-bg px-4 py-2 font-mono text-xs">
          error: {chat.error}
        </div>
      )}
    </div>
  );
}
