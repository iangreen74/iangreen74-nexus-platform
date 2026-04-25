import { useReducer } from 'react';
import { postMessage } from '../api';
import type { Turn, ChatResponse } from '../types';

interface ChatState {
  conversationId: string | null;
  turns: Turn[];
  loading: boolean;
  error: string | null;
}

type Action =
  | { type: 'send'; userMessage: string }
  | { type: 'response'; r: ChatResponse }
  | { type: 'error'; message: string }
  | { type: 'load'; conversationId: string; turns: Turn[] }
  | { type: 'reset' };

const initial: ChatState = {
  conversationId: null,
  turns: [],
  loading: false,
  error: null,
};

function reducer(s: ChatState, a: Action): ChatState {
  switch (a.type) {
    case 'send': {
      const userTurn: Turn = {
        turn_index: s.turns.length,
        role: 'user',
        content: a.userMessage,
        created_at: new Date().toISOString(),
      };
      return { ...s, turns: [...s.turns, userTurn], loading: true, error: null };
    }
    case 'response': {
      const echoTurn: Turn = {
        turn_index: s.turns.length,
        role: 'assistant',
        content: a.r.response,
        created_at: new Date().toISOString(),
        tool_calls: a.r.tool_calls,
      };
      return {
        ...s,
        conversationId: a.r.conversation_id,
        turns: [...s.turns, echoTurn],
        loading: false,
      };
    }
    case 'error':
      return { ...s, loading: false, error: a.message };
    case 'load':
      return { ...initial, conversationId: a.conversationId, turns: a.turns };
    case 'reset':
      return initial;
  }
}

export function useChat() {
  const [state, dispatch] = useReducer(reducer, initial);

  async function send(message: string) {
    dispatch({ type: 'send', userMessage: message });
    try {
      const r = await postMessage(state.conversationId, message);
      dispatch({ type: 'response', r });
    } catch (e) {
      dispatch({
        type: 'error',
        message: e instanceof Error ? e.message : 'unknown error',
      });
    }
  }

  function loadConversation(conversationId: string, turns: Turn[]) {
    dispatch({ type: 'load', conversationId, turns });
  }

  function reset() {
    dispatch({ type: 'reset' });
  }

  return { ...state, send, loadConversation, reset };
}
