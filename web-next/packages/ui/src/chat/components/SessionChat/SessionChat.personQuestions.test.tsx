import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionChat } from './SessionChat';
import { transformTurns } from '../../hooks/useSkuldChat';
import type { ConversationTurn } from '../../hooks/useSkuldChat';
import type { PersonQuestionAnswer } from '../../personQuestions';

/** The turn the broker writes for `ask_person`, as history delivers it. */
const QUESTION_TURN: ConversationTurn = {
  id: 'pq_q-1',
  role: 'assistant',
  content: 'Asked Alice Smith: Which branch should I use?',
  created_at: '2026-11-10T10:00:00Z',
  parts: [
    {
      type: 'tool_use',
      id: 'q-1',
      name: 'ask_person',
      input: {
        question_id: 'q-1',
        user_id: 'alice',
        display_name: 'Alice Smith',
        role: 'approver',
        question: 'Which branch should I use?',
      },
    },
  ],
  metadata: { person_question: 'q-1' },
};

const ANSWER: PersonQuestionAnswer = {
  content: 'Use main',
  answeredBy: { userId: 'bob', displayName: 'Bob Jones' },
};

function chat(props: Partial<React.ComponentProps<typeof SessionChat>> = {}) {
  return (
    <SessionChat
      messages={transformTurns([QUESTION_TURN])}
      onSend={vi.fn()}
      personQuestions={{ answers: new Map(), errors: new Map(), sending: new Set() }}
      {...props}
    />
  );
}

describe('SessionChat questions put to a person', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("shows the broker's ask_person turn as a card, not as a tool call", () => {
    render(chat({ viewerId: 'bob' }));

    const card = screen.getByTestId('person-question-card');
    expect(card).toHaveTextContent('Alice Smith');
    expect(card).toHaveTextContent('Which branch should I use?');
    expect(screen.queryByText('ask_person')).not.toBeInTheDocument();
    // The turn's own text ("Asked Alice Smith: ...") is the card, said once.
    expect(screen.getAllByText(/Which branch should I use\?/)).toHaveLength(1);
  });

  it('lets the person asked answer it', () => {
    const onAnswer = vi.fn();
    render(
      chat({
        viewerId: 'alice',
        personQuestions: {
          answers: new Map(),
          errors: new Map(),
          sending: new Set(),
          onAnswer,
        },
      }),
    );
    fireEvent.change(screen.getByRole('textbox', { name: /Answer Alice Smith/ }), {
      target: { value: 'Use main' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send answer' }));

    expect(onAnswer).toHaveBeenCalledWith('q-1', 'Use main');
  });

  it('lets a viewer who is neither the addressee nor an approver only read it', () => {
    render(
      chat({
        viewerId: 'bob',
        roomApprover: false,
        personQuestions: {
          answers: new Map(),
          errors: new Map(),
          sending: new Set(),
          onAnswer: vi.fn(),
        },
      }),
    );

    expect(screen.queryByRole('textbox', { name: /Answer Alice Smith/ })).not.toBeInTheDocument();
    expect(screen.getByTestId('person-question-who-can-answer')).toBeInTheDocument();
  });

  it("lets an approver answer someone else's question", () => {
    render(
      chat({
        viewerId: 'bob',
        roomApprover: true,
        personQuestions: {
          answers: new Map(),
          errors: new Map(),
          sending: new Set(),
          onAnswer: vi.fn(),
        },
      }),
    );

    expect(screen.getByRole('textbox', { name: /Answer Alice Smith/ })).toBeInTheDocument();
  });

  it('comes back answered from replayed history and frames, after a reload', () => {
    const answers = new Map([['q-1', ANSWER]]);
    const { unmount } = render(
      chat({
        viewerId: 'bob',
        personQuestions: { answers, errors: new Map(), sending: new Set() },
      }),
    );
    expect(screen.getByTestId('person-question-answer')).toHaveTextContent('Bob Jones');
    unmount();

    // A fresh mount with the same history and the same replayed frames is the same card.
    render(
      chat({
        viewerId: 'bob',
        personQuestions: { answers, errors: new Map(), sending: new Set() },
      }),
    );
    expect(screen.getByTestId('person-question-card')).toHaveAttribute('data-state', 'answered');
    expect(screen.getByTestId('person-question-answer')).toHaveTextContent('Answered by');
  });

  it('shows a conversation with no person-question state as waiting and read-only', () => {
    render(<SessionChat messages={transformTurns([QUESTION_TURN])} onSend={vi.fn()} />);

    expect(screen.getByTestId('person-question-waiting')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: /Answer Alice Smith/ })).not.toBeInTheDocument();
  });
});
