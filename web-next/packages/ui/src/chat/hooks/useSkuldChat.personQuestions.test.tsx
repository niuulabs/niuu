import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSkuldChat } from './useSkuldChat';

vi.mock('@niuulabs/query', () => ({
  getAccessToken: () => null,
  getAuthHeaders: (headers?: HeadersInit) => new Headers(headers),
}));

const sendJson = vi.fn();
let wsHandlers: {
  onOpen?: () => void;
  onMessage?: (raw: string) => void;
  onClose?: () => void;
  onError?: () => void;
} = {};

vi.mock('./useWebSocket', () => ({
  useWebSocket: (_url: string | null, handlers: typeof wsHandlers) => {
    wsHandlers = handlers;
    return { sendJson };
  },
}));

const URL_A = 'ws://localhost:8080/s/test/session';

function frame(value: Record<string, unknown>) {
  act(() => {
    wsHandlers.onMessage?.(JSON.stringify(value));
  });
}

async function connect(url: string = URL_A) {
  const hook = renderHook(
    ({ socketUrl }: { socketUrl: string }) => useSkuldChat(socketUrl, { historyMode: 'none' }),
    { initialProps: { socketUrl: url } },
  );
  await waitFor(() => expect(hook.result.current.historyLoaded).toBe(true));
  act(() => wsHandlers.onOpen?.());
  return hook;
}

const QUESTION_ID = 'q-1';

const QUESTION_TURN = {
  id: `pq_${QUESTION_ID}`,
  role: 'assistant',
  content: 'Asked Alice Smith: Which branch should I use?',
  created_at: '2026-11-10T10:00:00Z',
  parts: [
    {
      type: 'tool_use',
      id: QUESTION_ID,
      name: 'ask_person',
      input: {
        question_id: QUESTION_ID,
        user_id: 'alice',
        display_name: 'Alice Smith',
        role: 'approver',
        question: 'Which branch should I use?',
      },
    },
  ],
  metadata: { person_question: QUESTION_ID },
};

const ANSWERED = {
  type: 'person_answered',
  question_id: QUESTION_ID,
  message_id: 'm-1',
  content: 'Use main',
  author: { user_id: 'bob', display_name: 'Bob Jones' },
  addressee: { user_id: 'alice', display_name: 'Alice Smith' },
};

describe('useSkuldChat questions put to a person', () => {
  beforeEach(() => {
    sendJson.mockReset();
    wsHandlers = {};
    sessionStorage.clear();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, json: async () => ({ turns: [] }) })),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('the question', () => {
    it("shows live as the broker's ask_person turn", async () => {
      const { result } = await connect();
      frame({ type: 'conversation.turn', turn: QUESTION_TURN });

      expect(result.current.messages).toHaveLength(1);
      expect(result.current.messages[0]).toMatchObject({
        id: 'pq_q-1',
        role: 'assistant',
        parts: [{ type: 'tool_use', name: 'ask_person' }],
      });
    });

    it('comes back from history', async () => {
      const { result } = await connect();
      frame({ type: 'conversation_history', turns: [QUESTION_TURN] });

      expect(result.current.messages.map((message) => message.id)).toEqual(['pq_q-1']);
    });

    it('is not added twice when the live turn is repeated', async () => {
      const { result } = await connect();
      frame({ type: 'conversation.turn', turn: QUESTION_TURN });
      frame({ type: 'conversation.turn', turn: QUESTION_TURN });

      expect(result.current.messages).toHaveLength(1);
    });
  });

  describe('the answer', () => {
    it('starts with no answers', async () => {
      const { result } = await connect();
      expect(result.current.personAnswers.size).toBe(0);
      expect(result.current.personAnswerErrors.size).toBe(0);
      expect(result.current.personAnswersSending.size).toBe(0);
    });

    it('records who answered and with what, from the person_answered frame', async () => {
      const { result } = await connect();
      frame(ANSWERED);

      expect(result.current.personAnswers.get(QUESTION_ID)).toEqual({
        content: 'Use main',
        answeredBy: { userId: 'bob', displayName: 'Bob Jones' },
      });
    });

    it('survives history being loaded after the frame (replay in either order)', async () => {
      const { result } = await connect();
      frame(ANSWERED);
      frame({ type: 'conversation_history', turns: [QUESTION_TURN] });

      expect(result.current.messages.map((message) => message.id)).toEqual(['pq_q-1']);
      expect(result.current.personAnswers.get(QUESTION_ID)?.content).toBe('Use main');
    });

    it('survives history being loaded before the frame', async () => {
      const { result } = await connect();
      frame({ type: 'conversation_history', turns: [QUESTION_TURN] });
      frame(ANSWERED);
      frame({ type: 'conversation_history', turns: [QUESTION_TURN] });

      expect(result.current.personAnswers.get(QUESTION_ID)?.answeredBy.displayName).toBe(
        'Bob Jones',
      );
    });

    it("ignores a person_answered frame that is not shaped like the broker's", async () => {
      const { result } = await connect();
      frame({ type: 'person_answered', question_id: QUESTION_ID, content: 'x' });
      frame({ type: 'person_answered', content: 'x', author: { user_id: 'bob' } });

      expect(result.current.personAnswers.size).toBe(0);
    });
  });

  describe('sending an answer', () => {
    it('posts person_answer with the question, the trimmed text and a request id', async () => {
      const { result } = await connect();
      act(() => result.current.sendPersonAnswer(QUESTION_ID, '  Use main  '));

      expect(sendJson).toHaveBeenCalledWith({
        type: 'person_answer',
        question_id: QUESTION_ID,
        content: 'Use main',
        request_id: expect.any(String),
      });
      expect(result.current.personAnswersSending.has(QUESTION_ID)).toBe(true);
    });

    it('sends nothing for an empty answer', async () => {
      const { result } = await connect();
      act(() => result.current.sendPersonAnswer(QUESTION_ID, '   '));

      expect(sendJson).not.toHaveBeenCalled();
      expect(result.current.personAnswersSending.size).toBe(0);
    });

    it('is no longer sending once the broker accepted it', async () => {
      const { result } = await connect();
      act(() => result.current.sendPersonAnswer(QUESTION_ID, 'Use main'));
      frame(ANSWERED);

      expect(result.current.personAnswersSending.size).toBe(0);
      expect(result.current.personAnswers.has(QUESTION_ID)).toBe(true);
    });

    it("shows the broker's reason on the question, not in the conversation", async () => {
      const { result } = await connect();
      act(() => result.current.sendPersonAnswer(QUESTION_ID, 'Use main'));
      const requestId = sendJson.mock.calls.at(-1)?.[0].request_id as string;
      frame({
        type: 'error',
        code: 'forbidden',
        request_id: requestId,
        content: 'This question is for Alice Smith; only they may answer it',
      });

      expect(result.current.personAnswerErrors.get(QUESTION_ID)).toBe(
        'This question is for Alice Smith; only they may answer it',
      );
      expect(result.current.personAnswersSending.size).toBe(0);
      expect(result.current.messages).toEqual([]);
    });

    it('clears the reason when the person tries again, with a new request id', async () => {
      const { result } = await connect();
      act(() => result.current.sendPersonAnswer(QUESTION_ID, 'Use main'));
      const first = sendJson.mock.calls.at(-1)?.[0].request_id as string;
      frame({ type: 'error', code: 'forge_unavailable', request_id: first, content: 'try again' });
      act(() => result.current.sendPersonAnswer(QUESTION_ID, 'Use main'));
      const second = sendJson.mock.calls.at(-1)?.[0].request_id as string;

      expect(second).not.toBe(first);
      expect(result.current.personAnswerErrors.size).toBe(0);
      expect(result.current.personAnswersSending.has(QUESTION_ID)).toBe(true);
    });

    it('drops a stale reason when the answer arrives', async () => {
      const { result } = await connect();
      act(() => result.current.sendPersonAnswer(QUESTION_ID, 'Use main'));
      const requestId = sendJson.mock.calls.at(-1)?.[0].request_id as string;
      frame({
        type: 'error',
        code: 'conflict',
        request_id: requestId,
        content: 'already answered',
      });
      frame(ANSWERED);

      expect(result.current.personAnswerErrors.size).toBe(0);
    });
  });

  describe('who decided', () => {
    it('names the person who decided a permission prompt', async () => {
      const { result } = await connect();
      frame({
        type: 'control_request',
        request_id: 'perm-1',
        tool: 'Bash',
        input: { command: 'ls' },
      });
      frame({
        type: 'permission_resolved',
        request_id: 'perm-1',
        behavior: 'allow',
        auto_approved: false,
        decided_by: { user_id: 'alice', display_name: 'Alice Smith' },
      });

      expect(result.current.pendingPermissions).toEqual([]);
      expect(result.current.messages).toHaveLength(1);
      expect(result.current.messages[0]).toMatchObject({
        id: 'decision-perm-1',
        role: 'system',
        content: 'Alice Smith allowed Bash',
      });
    });

    it('says denied for a denial', async () => {
      const { result } = await connect();
      frame({ type: 'control_request', request_id: 'perm-1', tool: 'Bash', input: {} });
      frame({
        type: 'permission_resolved',
        request_id: 'perm-1',
        behavior: 'deny',
        decided_by: { user_id: 'alice', display_name: 'Alice Smith' },
      });

      expect(result.current.messages[0]?.content).toBe('Alice Smith denied Bash');
    });

    it('names nobody for an auto-approval or a decision with no verified person', async () => {
      const { result } = await connect();
      frame({ type: 'control_request', request_id: 'perm-1', tool: 'Bash', input: {} });
      frame({
        type: 'permission_resolved',
        request_id: 'perm-1',
        behavior: 'allow',
        auto_approved: true,
      });

      expect(result.current.pendingPermissions).toEqual([]);
      expect(result.current.messages).toEqual([]);
    });

    it('notes a decision once even when its frame is replayed', async () => {
      const { result } = await connect();
      const resolved = {
        type: 'permission_resolved',
        request_id: 'perm-9',
        behavior: 'allow',
        decided_by: { user_id: 'alice', display_name: 'Alice Smith' },
      };
      frame(resolved);
      frame(resolved);

      expect(result.current.messages).toHaveLength(1);
      expect(result.current.messages[0]?.content).toBe('Alice Smith allowed a tool request');
    });

    it('names the person who answered a question the agent asked the room', async () => {
      const { result } = await connect();
      frame({
        type: 'ask_user_question',
        request_id: 'ask-1',
        questions: [{ question: 'Which branch?', options: [{ label: 'main' }] }],
      });
      frame({
        type: 'ask_user_answered',
        request_id: 'ask-1',
        decided_by: { user_id: 'bob', display_name: 'Bob Jones' },
      });

      expect(result.current.messages[0]).toMatchObject({
        id: 'decision-ask-1',
        content: 'Bob Jones answered: Which branch?',
      });
    });

    it('still names them when the answer already removed the question from the pending list', async () => {
      const { result } = await connect();
      frame({
        type: 'ask_user_question',
        request_id: 'ask-1',
        questions: [{ question: 'Which branch?', options: [{ label: 'main' }] }],
      });
      act(() => result.current.respondToInput('ask-1', ['main']));
      frame({
        type: 'ask_user_answered',
        request_id: 'ask-1',
        decided_by: { user_id: 'bob', display_name: 'Bob Jones' },
      });

      expect(result.current.messages[0]?.content).toBe('Bob Jones answered: Which branch?');
    });

    it('notes an answer to a question it never saw without inventing the question', async () => {
      const { result } = await connect();
      frame({
        type: 'ask_user_answered',
        request_id: 'ask-2',
        decided_by: { user_id: 'bob', display_name: 'Bob Jones' },
      });
      frame({ type: 'ask_user_answered', request_id: 'ask-3' });
      frame({ type: 'ask_user_answered', decided_by: { user_id: 'bob' } });

      expect(result.current.messages).toHaveLength(1);
      expect(result.current.messages[0]?.content).toBe('Bob Jones answered a question');
    });
  });
});
