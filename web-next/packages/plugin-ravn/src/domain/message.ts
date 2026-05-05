import { z } from 'zod';

/**
 * Role/kind of a message in a Session transcript.
 *
 * user        — message from the human operator
 * asst        — response from the assistant (persona)
 * system      — injected system context
 * tool_call   — the agent invoking a tool
 * tool_result — the tool's response payload
 * emit        — the ravn emitting a domain event
 * think       — extended-thinking scratchpad (hidden in collapsed view)
 */
export const messageKindSchema = z.enum([
  'user',
  'asst',
  'system',
  'tool_call',
  'tool_result',
  'emit',
  'think',
]);

export type MessageKind = z.infer<typeof messageKindSchema>;

/**
 * A single entry in a Session transcript.
 *
 * Owner: plugin-ravn (SessionStream, SessionsView).
 */
export const messageSchema = z.object({
  /** Unique identifier (UUID). */
  id: z.string().uuid(),
  /** Session this message belongs to. */
  sessionId: z.string().min(1),
  /** Message role/kind. */
  kind: messageKindSchema,
  /** Text content (markdown for user/asst/system, JSON for tool payloads). */
  content: z.string(),
  /** ISO-8601 UTC timestamp when this message was created. */
  ts: z.string().datetime(),
  /** Tool name — populated when kind is tool_call or tool_result. */
  toolName: z.string().optional(),
});

export type Message = z.infer<typeof messageSchema>;
