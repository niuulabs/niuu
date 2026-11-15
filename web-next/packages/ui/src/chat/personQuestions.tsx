import { createContext, useContext, type ReactNode } from 'react';
import type { MessageAuthor } from './types';
import { parseMessageAuthor } from './hooks/sessionFrames';

/**
 * The tool_use part the broker writes for a question the agent put to one person
 * (`src/skuld/person_questions.py`, `PERSON_QUESTION_TOOL_NAME`).
 */
export const PERSON_QUESTION_TOOL_NAME = 'ask_person';

/** The browser message that answers a question put to a person. */
export const PERSON_ANSWER_MESSAGE = 'person_answer';

/** The durable frame the broker emits when a question was answered. */
export const PERSON_ANSWERED_FRAME = 'person_answered';

/** A question put to a person, as the broker's `ask_person` part carries it. */
export interface PersonQuestion {
  /** The broker's id for the question; the answer names it. */
  questionId: string;
  /** Who was asked. */
  addressee: MessageAuthor;
  /** The addressee's role on the project, when the broker knew it. */
  role?: string;
  question: string;
}

/** The answer the broker accepted, and the person behind the connection that gave it. */
export interface PersonQuestionAnswer {
  content: string;
  answeredBy: MessageAuthor;
}

/** The part's `input`: `{question_id, user_id, display_name, role, question}`. */
export function parsePersonQuestionInput(input: unknown): PersonQuestion | null {
  if (!input || typeof input !== 'object') return null;
  const raw = input as Record<string, unknown>;
  const questionId = typeof raw.question_id === 'string' ? raw.question_id : '';
  const question = typeof raw.question === 'string' ? raw.question : '';
  const addressee = parseMessageAuthor({ user_id: raw.user_id, display_name: raw.display_name });
  if (!questionId || !question || !addressee) return null;
  const role = typeof raw.role === 'string' && raw.role ? raw.role : undefined;
  return { questionId, addressee, question, ...(role ? { role } : {}) };
}

/** A `person_answered` frame: `{question_id, content, author, message_id, addressee}`. */
export function parsePersonAnsweredFrame(
  frame: Record<string, unknown>,
): { questionId: string; answer: PersonQuestionAnswer } | null {
  const questionId = typeof frame.question_id === 'string' ? frame.question_id : '';
  const content = typeof frame.content === 'string' ? frame.content : '';
  const answeredBy = parseMessageAuthor(frame.author);
  if (!questionId || !content || !answeredBy) return null;
  return { questionId, answer: { content, answeredBy } };
}

/** The broker turn that records a question put to a person (live `conversation.turn`). */
export function isPersonQuestionTurn(turn: {
  id?: unknown;
  parts?: Array<Record<string, unknown>>;
}): boolean {
  if (typeof turn.id !== 'string' || !turn.id) return false;
  return (
    turn.parts?.some(
      (part) => part.type === 'tool_use' && part.name === PERSON_QUESTION_TOOL_NAME,
    ) ?? false
  );
}

/** What the question cards need from the conversation they are shown in. */
/**
 * The answer to a question put to a person is the answering person's own message in the
 * conversation, with the id ``pa_<question id>`` (the broker's ``person_answer_turn_id``).
 */
export const PERSON_ANSWER_MESSAGE_PREFIX = 'pa_';

/** The question a message answers, from its id, or null for any other message. */
export function answeredQuestionId(messageId: string): string | null {
  return messageId.startsWith(PERSON_ANSWER_MESSAGE_PREFIX)
    ? messageId.slice(PERSON_ANSWER_MESSAGE_PREFIX.length) || null
    : null;
}

/** The DOM id of a conversation message, so a card can bring its answer into view. */
export function chatMessageDomId(messageId: string): string {
  return `chat-message-${messageId}`;
}

export interface PersonQuestionsState {
  /** The signed-in person; the addressee of a question may always answer it. */
  viewerId?: string;
  /** The viewer holds the approver room role or above, so may answer any question. */
  approver: boolean;
  /** The accepted answers by question id, from live frames and from replay. */
  answers: ReadonlyMap<string, PersonQuestionAnswer>;
  /** Why the broker refused the last answer to a question, by question id. */
  errors: ReadonlyMap<string, string>;
  /** Questions whose answer has been sent and not yet accepted or refused. */
  sending: ReadonlySet<string>;
  /** Sends the answer. Absent when the conversation cannot be answered in (a replay). */
  onAnswer?: (questionId: string, content: string) => void;
}

const NO_ANSWERS: ReadonlyMap<string, never> = new Map<string, never>();
const NO_SENDING: ReadonlySet<string> = new Set();

const READ_ONLY: PersonQuestionsState = {
  approver: false,
  answers: NO_ANSWERS,
  errors: NO_ANSWERS,
  sending: NO_SENDING,
};

const PersonQuestionsContext = createContext<PersonQuestionsState>(READ_ONLY);

export function PersonQuestionsProvider({
  value,
  children,
}: {
  value: PersonQuestionsState;
  children: ReactNode;
}) {
  return (
    <PersonQuestionsContext.Provider value={value}>{children}</PersonQuestionsContext.Provider>
  );
}

export function usePersonQuestions(): PersonQuestionsState {
  return useContext(PersonQuestionsContext);
}

/** The addressee, or anyone with the approver room role or above (the broker enforces it). */
export function mayAnswerPersonQuestion(
  state: Pick<PersonQuestionsState, 'viewerId' | 'approver' | 'onAnswer'>,
  question: PersonQuestion,
): boolean {
  if (!state.onAnswer) return false;
  return state.approver || state.viewerId === question.addressee.userId;
}
