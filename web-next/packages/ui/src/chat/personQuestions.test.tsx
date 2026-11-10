import { describe, expect, it, vi } from 'vitest';
import {
  isPersonQuestionTurn,
  mayAnswerPersonQuestion,
  parsePersonAnsweredFrame,
  parsePersonQuestionInput,
  type PersonQuestion,
} from './personQuestions';

const INPUT = {
  question_id: 'q-1',
  user_id: 'alice',
  display_name: 'Alice Smith',
  role: 'approver',
  question: 'Which branch?',
};

describe('parsePersonQuestionInput', () => {
  it('reads the ask_person part the broker writes', () => {
    expect(parsePersonQuestionInput(INPUT)).toEqual({
      questionId: 'q-1',
      addressee: { userId: 'alice', displayName: 'Alice Smith' },
      role: 'approver',
      question: 'Which branch?',
    });
  });

  it('leaves out a role the broker did not know, and keeps a nameless addressee by id', () => {
    const parsed = parsePersonQuestionInput({ ...INPUT, role: '', display_name: '' });
    expect(parsed).toEqual({
      questionId: 'q-1',
      addressee: { userId: 'alice', displayName: '' },
      question: 'Which branch?',
    });
  });

  it.each([
    ['nothing', undefined],
    ['a string', 'q'],
    ['no question id', { ...INPUT, question_id: '' }],
    ['no question', { ...INPUT, question: '' }],
    ['no addressee', { ...INPUT, user_id: '' }],
  ])('is not a question with %s', (_name, input) => {
    expect(parsePersonQuestionInput(input)).toBeNull();
  });
});

describe('parsePersonAnsweredFrame', () => {
  it('reads who answered and with what', () => {
    expect(
      parsePersonAnsweredFrame({
        type: 'person_answered',
        question_id: 'q-1',
        content: 'Use main',
        author: { user_id: 'bob', display_name: 'Bob Jones' },
      }),
    ).toEqual({
      questionId: 'q-1',
      answer: { content: 'Use main', answeredBy: { userId: 'bob', displayName: 'Bob Jones' } },
    });
  });

  it.each([
    ['no question id', { content: 'x', author: { user_id: 'bob' } }],
    ['no content', { question_id: 'q-1', author: { user_id: 'bob' } }],
    ['no author', { question_id: 'q-1', content: 'x' }],
  ])('is not an answer with %s', (_name, frame) => {
    expect(parsePersonAnsweredFrame(frame)).toBeNull();
  });
});

describe('isPersonQuestionTurn', () => {
  it('is a turn with an ask_person tool_use part', () => {
    expect(
      isPersonQuestionTurn({ id: 'pq_q-1', parts: [{ type: 'tool_use', name: 'ask_person' }] }),
    ).toBe(true);
  });

  it.each([
    ['another tool', { id: 't', parts: [{ type: 'tool_use', name: 'Bash' }] }],
    ['a text part', { id: 't', parts: [{ type: 'text', name: 'ask_person' }] }],
    ['no parts', { id: 't' }],
    ['no id', { parts: [{ type: 'tool_use', name: 'ask_person' }] }],
  ])('is not one for %s', (_name, turn) => {
    expect(isPersonQuestionTurn(turn)).toBe(false);
  });
});

describe('mayAnswerPersonQuestion', () => {
  const question = parsePersonQuestionInput(INPUT) as PersonQuestion;
  const onAnswer = vi.fn();

  it('lets the addressee answer', () => {
    expect(
      mayAnswerPersonQuestion({ viewerId: 'alice', approver: false, onAnswer }, question),
    ).toBe(true);
  });

  it('lets an approver or owner answer', () => {
    expect(mayAnswerPersonQuestion({ viewerId: 'bob', approver: true, onAnswer }, question)).toBe(
      true,
    );
  });

  it('does not let anyone else answer', () => {
    expect(mayAnswerPersonQuestion({ viewerId: 'bob', approver: false, onAnswer }, question)).toBe(
      false,
    );
    expect(mayAnswerPersonQuestion({ approver: false, onAnswer }, question)).toBe(false);
  });

  it('lets nobody answer where the conversation cannot be answered in', () => {
    expect(mayAnswerPersonQuestion({ viewerId: 'alice', approver: true }, question)).toBe(false);
  });
});
