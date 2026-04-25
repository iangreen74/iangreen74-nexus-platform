import type {
  ChatResponse,
  ConversationsResponse,
  ConversationDetail,
  ConversationSummary,
  EchoHealth,
} from './types';

const BASE = '/api/v2/echo';

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function asJson<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new ApiError(r.status, `${r.status} ${r.statusText}: ${text.slice(0, 200)}`);
  }
  return (await r.json()) as T;
}

export async function postMessage(
  conversationId: string | null,
  message: string
): Promise<ChatResponse> {
  const r = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId, message }),
  });
  return asJson<ChatResponse>(r);
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const r = await fetch(`${BASE}/conversations`);
  const wrapper = await asJson<ConversationsResponse>(r);
  return wrapper.conversations ?? [];
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  const r = await fetch(`${BASE}/conversations/${id}`);
  return asJson<ConversationDetail>(r);
}

export async function getEchoHealth(): Promise<EchoHealth> {
  try {
    const r = await fetch(`${BASE}/health`);
    return await asJson<EchoHealth>(r);
  } catch {
    return { status: 'unreachable', subsystem: 'echo', version: 'unknown' };
  }
}

export { ApiError };
