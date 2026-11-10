import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PersonQuestionCard } from './PersonQuestionCard';
import {
  PersonQuestionsProvider,
  parsePersonQuestionInput,
  type PersonQuestion,
  type PersonQuestionsState,
} from '../../personQuestions';

const QUESTION = parsePersonQuestionInput({
  question_id: 'q-1',
  user_id: 'alice',
  display_name: 'Alice Smith',
  role: 'approver',
  question: 'Which branch should I use?',
}) as PersonQuestion;

function renderCard(state: Partial<PersonQuestionsState> = {}) {
  const onAnswer = vi.fn();
  const value: PersonQuestionsState = {
    approver: false,
    answers: new Map(),
    errors: new Map(),
    sending: new Set(),
    onAnswer,
    ...state,
  };
  render(
    <PersonQuestionsProvider value={value}>
      <PersonQuestionCard question={QUESTION} />
    </PersonQuestionsProvider>,
  );
  return { onAnswer };
}

const answerBox = () => screen.queryByRole('textbox', { name: /Answer Alice Smith/ });

describe('PersonQuestionCard', () => {
  describe('what it shows', () => {
    it('names who was asked, with their avatar and role, and the question', () => {
      renderCard({ viewerId: 'bob' });
      const card = screen.getByTestId('person-question-card');

      expect(card).toHaveAttribute('aria-label', 'Question for Alice Smith');
      expect(card).toHaveTextContent('Asked');
      expect(card).toHaveTextContent('Alice Smith');
      expect(card).toHaveTextContent('approver');
      expect(card).toHaveTextContent('Which branch should I use?');
      expect(card.querySelector('.niuu-chat-speaker-avatar')).toHaveAttribute('data-initial', 'AS');
    });

    it('is waiting until an answer arrives', () => {
      renderCard({ viewerId: 'bob' });

      expect(screen.getByTestId('person-question-card')).toHaveAttribute('data-state', 'waiting');
      expect(screen.getByTestId('person-question-waiting')).toHaveTextContent(
        'Waiting for Alice Smith',
      );
    });

    it('says who answered and what, once answered, and offers no answer box', () => {
      renderCard({
        viewerId: 'alice',
        answers: new Map([
          ['q-1', { content: 'Use main', answeredBy: { userId: 'bob', displayName: 'Bob Jones' } }],
        ]),
      });

      expect(screen.getByTestId('person-question-card')).toHaveAttribute('data-state', 'answered');
      const answer = screen.getByTestId('person-question-answer');
      expect(answer).toHaveTextContent('Answered by');
      expect(answer).toHaveTextContent('Bob Jones');
      expect(answer).toHaveTextContent('Use main');
      expect(screen.queryByTestId('person-question-waiting')).not.toBeInTheDocument();
      expect(answerBox()).not.toBeInTheDocument();
    });

    it('shows an answer for another question as nothing', () => {
      renderCard({
        answers: new Map([
          ['q-2', { content: 'x', answeredBy: { userId: 'bob', displayName: 'Bob' } }],
        ]),
      });

      expect(screen.getByTestId('person-question-card')).toHaveAttribute('data-state', 'waiting');
    });

    it('names a person with no display name by their id', () => {
      render(
        <PersonQuestionsProvider
          value={{
            approver: false,
            answers: new Map([
              ['q-1', { content: 'ok', answeredBy: { userId: 'u-77', displayName: '' } }],
            ]),
            errors: new Map(),
            sending: new Set(),
          }}
        >
          <PersonQuestionCard question={QUESTION} />
        </PersonQuestionsProvider>,
      );

      expect(screen.getByTestId('person-question-answer')).toHaveTextContent('u-77');
    });
  });

  describe('who gets the answer box', () => {
    it('the person asked', () => {
      renderCard({ viewerId: 'alice' });
      expect(answerBox()).toBeInTheDocument();
      expect(screen.queryByTestId('person-question-who-can-answer')).not.toBeInTheDocument();
    });

    it('an approver or owner', () => {
      renderCard({ viewerId: 'bob', approver: true });
      expect(answerBox()).toBeInTheDocument();
    });

    it('nobody else, who is told who can answer', () => {
      renderCard({ viewerId: 'bob', approver: false });

      expect(answerBox()).not.toBeInTheDocument();
      expect(screen.getByTestId('person-question-who-can-answer')).toHaveTextContent(
        'Only Alice Smith, or someone who can approve in this session, can answer.',
      );
    });

    it('nobody, with no line either, where the conversation cannot be answered in', () => {
      renderCard({ viewerId: 'alice', approver: true, onAnswer: undefined });

      expect(answerBox()).not.toBeInTheDocument();
      expect(screen.queryByTestId('person-question-who-can-answer')).not.toBeInTheDocument();
      expect(screen.getByTestId('person-question-waiting')).toBeInTheDocument();
    });
  });

  describe('answering', () => {
    it('sends the trimmed answer for this question', () => {
      const { onAnswer } = renderCard({ viewerId: 'alice' });
      fireEvent.change(answerBox()!, { target: { value: '  Use main  ' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send answer' }));

      expect(onAnswer).toHaveBeenCalledExactlyOnceWith('q-1', 'Use main');
    });

    it('cannot send an empty answer', () => {
      const { onAnswer } = renderCard({ viewerId: 'alice' });
      const send = screen.getByRole('button', { name: 'Send answer' });
      expect(send).toBeDisabled();

      fireEvent.change(answerBox()!, { target: { value: '   ' } });
      expect(send).toBeDisabled();
      fireEvent.submit(answerBox()!.closest('form')!);
      expect(onAnswer).not.toHaveBeenCalled();
    });

    it('is disabled while the answer is on its way', () => {
      const { onAnswer } = renderCard({ viewerId: 'alice', sending: new Set(['q-1']) });

      expect(answerBox()).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Sending…' })).toBeDisabled();
      fireEvent.submit(answerBox()!.closest('form')!);
      expect(onAnswer).not.toHaveBeenCalled();
    });

    it("shows the broker's reason inline and lets the person try again", () => {
      const { onAnswer } = renderCard({
        viewerId: 'alice',
        errors: new Map([['q-1', 'The question was already answered by Bob Jones']]),
      });

      expect(screen.getByRole('alert')).toHaveTextContent(
        'The question was already answered by Bob Jones',
      );
      fireEvent.change(answerBox()!, { target: { value: 'Use main' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send answer' }));
      expect(onAnswer).toHaveBeenCalledWith('q-1', 'Use main');
    });

    it('shows no reason that belongs to another question', () => {
      renderCard({ viewerId: 'alice', errors: new Map([['q-2', 'nope']]) });
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
  });

  it('is read-only without a provider', () => {
    render(<PersonQuestionCard question={QUESTION} />);

    expect(screen.getByTestId('person-question-waiting')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });
});
