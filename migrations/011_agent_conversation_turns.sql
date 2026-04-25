-- 011_agent_conversation_turns.sql
-- V2 persisted turns of the agent chat (user, assistant, tool calls, tool results).

CREATE TABLE IF NOT EXISTS agent_conversation_turns (
    turn_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id  UUID NOT NULL,
    turn_index       INTEGER NOT NULL,
    role             TEXT NOT NULL,
    content          JSONB NOT NULL,
    tool_calls       JSONB,
    tokens_in        INTEGER,
    tokens_out       INTEGER,
    cost_usd         NUMERIC(10, 4),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_act_conv ON agent_conversation_turns (conversation_id, turn_index);

CREATE TABLE IF NOT EXISTS agent_conversations (
    conversation_id  UUID PRIMARY KEY,
    title            TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    turn_count       INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'active',
    tags             JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_ac_active ON agent_conversations (last_active_at DESC) WHERE status = 'active';
