// Echo API response shapes. Mirrors nexus/dashboard/echo_routes.py Pydantic
// models and nexus/aria_v2/reasoner.py.tool_calls_made structure.
//
// VERIFIED against real reasoner output 2026-04-25:
//   tool_calls[i] = { tool_use_id, tool_name, input, outcome: { ok, value, error } }
// (NOT { name, result } as some draft TS templates have suggested.)

export interface ToolOutcome {
  ok: boolean;
  value?: unknown;
  error?: string | null;
}

export interface ToolCall {
  tool_use_id: string;
  tool_name: string;
  input: Record<string, unknown>;
  outcome: ToolOutcome;
}

export interface ChatResponse {
  conversation_id: string;
  response: string;
  tool_calls: ToolCall[];
  rounds: number;
  tokens_in: number;
  tokens_out: number;
  error?: string | null;
}

export interface ConversationSummary {
  conversation_id: string;
  title: string | null;
  started_at?: string;
  last_active_at: string;
  turn_count: number;
  status?: string;
}

// Wire shape returned by the FastAPI persistence layer.
export interface PersistedTurn {
  turn_index: number;
  role: string;
  content: { text?: string; results?: unknown[] } | string;
  tool_calls?: ToolCall[];
  tokens_in?: number;
  tokens_out?: number;
  cost_usd?: number;
  created_at?: string;
}

// Local UI shape — flattened content + role narrowed.
export interface Turn {
  turn_index: number;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  created_at?: string;
  tool_calls?: ToolCall[];
}

export interface ConversationDetail {
  conversation_id: string;
  turn_count: number;
  turns: PersistedTurn[];
}

export interface EchoHealth {
  status: string;
  subsystem: string;
  version: string;
}

// API wrapper shapes — the GET /conversations route wraps in { conversations: [] }.
export interface ConversationsResponse {
  conversations: ConversationSummary[];
}

// Helper to coerce a PersistedTurn into the simpler UI Turn shape.
export function flattenTurn(t: PersistedTurn): Turn {
  let role: Turn['role'] = 'assistant';
  if (t.role === 'user') role = 'user';
  else if (t.role === 'assistant') role = 'assistant';
  else role = 'tool';

  let content = '';
  if (typeof t.content === 'string') {
    content = t.content;
  } else if (t.content && typeof t.content === 'object') {
    if ('text' in t.content && typeof t.content.text === 'string') {
      content = t.content.text;
    } else {
      content = JSON.stringify(t.content);
    }
  }
  return {
    turn_index: t.turn_index,
    role,
    content,
    created_at: t.created_at,
    tool_calls: t.tool_calls,
  };
}
