import { useId, useState, type CSSProperties, type FormEvent } from 'react';
import { MessageCircleQuestion } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { Field } from '../../../primitives/Field';
import { Textarea } from '../../../primitives/Textarea';
import { authorInitials, authorName } from '../../authorship';
import { resolveParticipantColor } from '../../utils/participantColor';
import {
  mayAnswerPersonQuestion,
  usePersonQuestions,
  type PersonQuestion,
} from '../../personQuestions';
import type { MessageAuthor } from '../../types';

const BUTTON =
  'niuu:inline-flex niuu:items-center niuu:rounded-md niuu:border niuu:border-solid niuu:border-brand niuu:bg-brand/10 niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:font-medium niuu:text-text-primary niuu:cursor-pointer niuu:disabled:cursor-not-allowed niuu:disabled:opacity-60';

/** A person's initials on their colour, then their name: the avatar a message author wears. */
function Person({ person }: { person: MessageAuthor }) {
  return (
    <span
      className="niuu-chat-speaker niuu-chat-user-author"
      style={{ '--niuu-chat-engine': resolveParticipantColor(person.userId) } as CSSProperties}
    >
      <i
        className="niuu-chat-speaker-avatar niuu-chat-user-author-avatar"
        aria-hidden="true"
        data-initial={authorInitials(person)}
      />
      {authorName(person)}
    </span>
  );
}

function AnswerForm({
  question,
  sending,
  error,
  onAnswer,
}: {
  question: PersonQuestion;
  sending: boolean;
  error: string | undefined;
  onAnswer: (questionId: string, content: string) => void;
}) {
  const baseId = useId();
  const [content, setContent] = useState('');

  function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = content.trim();
    if (!trimmed || sending) return;
    onAnswer(question.questionId, trimmed);
  }

  return (
    <form className="niuu:flex niuu:flex-col niuu:gap-2" onSubmit={submit} noValidate>
      <Field id={`${baseId}-answer`} label={`Answer ${authorName(question.addressee)}'s question`}>
        <Textarea
          rows={2}
          value={content}
          onChange={(event) => setContent(event.target.value)}
          disabled={sending}
        />
      </Field>
      {error && (
        <p
          role="alert"
          data-testid="person-question-error"
          className="niuu:m-0 niuu:text-sm niuu:text-critical"
        >
          {error}
        </p>
      )}
      <div>
        <button type="submit" className={BUTTON} disabled={sending || content.trim() === ''}>
          {sending ? 'Sending…' : 'Send answer'}
        </button>
      </div>
    </form>
  );
}

export interface PersonQuestionCardProps {
  question: PersonQuestion;
  className?: string;
}

/**
 * A question the agent put to one person of the project: who was asked, the question, and
 * whether it is waiting or answered (and by whom, with what). The addressee, or anyone with
 * the approver room role or above, gets an answer box; everyone else is told who can answer.
 * The broker decides the answer; a refusal it sends is shown here, not in the transcript.
 */
export function PersonQuestionCard({ question, className }: PersonQuestionCardProps) {
  const state = usePersonQuestions();
  const answer = state.answers.get(question.questionId);
  const name = authorName(question.addressee);
  const canAnswer = mayAnswerPersonQuestion(state, question);

  return (
    <article
      data-testid="person-question-card"
      data-state={answer ? 'answered' : 'waiting'}
      aria-label={`Question for ${name}`}
      className={cn(
        'niuu:my-3 niuu:flex niuu:flex-col niuu:gap-2 niuu:rounded-lg niuu:border niuu:border-solid niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-3',
        className,
      )}
    >
      <header className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2 niuu:text-xs niuu:text-text-muted">
        <MessageCircleQuestion
          className="niuu:h-4 niuu:w-4 niuu:shrink-0 niuu:text-brand"
          aria-hidden="true"
        />
        <span className="niuu:font-medium niuu:uppercase niuu:tracking-wide">Asked</span>
        <Person person={question.addressee} />
        {question.role && <span>{question.role}</span>}
      </header>
      <p className="niuu:m-0 niuu:text-sm niuu:text-text-primary niuu:break-words">
        {question.question}
      </p>
      {answer ? (
        <div
          data-testid="person-question-answer"
          className="niuu:flex niuu:flex-col niuu:gap-1 niuu:border-t niuu:border-solid niuu:border-border-subtle niuu:pt-2 niuu:text-sm"
        >
          <span className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs niuu:text-text-muted">
            Answered by <Person person={answer.answeredBy} />
          </span>
          <p className="niuu:m-0 niuu:text-text-primary niuu:break-words">{answer.content}</p>
        </div>
      ) : (
        <>
          <p
            data-testid="person-question-waiting"
            className="niuu:m-0 niuu:text-xs niuu:text-text-muted"
          >
            Waiting for {name}
          </p>
          {canAnswer && state.onAnswer ? (
            <AnswerForm
              question={question}
              sending={state.sending.has(question.questionId)}
              error={state.errors.get(question.questionId)}
              onAnswer={state.onAnswer}
            />
          ) : (
            state.onAnswer && (
              <p
                data-testid="person-question-who-can-answer"
                className="niuu:m-0 niuu:text-xs niuu:text-text-muted"
              >
                Only {name}, or someone who can approve in this session, can answer.
              </p>
            )
          )}
        </>
      )}
    </article>
  );
}
