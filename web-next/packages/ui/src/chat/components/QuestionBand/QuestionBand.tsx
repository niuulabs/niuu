import { useEffect, useId, useState, type FormEvent } from 'react';
import { Check, CircleHelp } from 'lucide-react';
import { cn } from '../../../utils/cn';
import type { InputAnswer, InputRequest } from '../../types';
import './QuestionBand.css';

/** How often the "waiting" time on the band is refreshed. */
const WAITING_TICK_MS = 1000;
const SECONDS_PER_MINUTE = 60;
const MINUTES_PER_HOUR = 60;
const MS_PER_SECOND = 1000;

const RECOMMENDED_SUFFIX = '(recommended)';

/** "Recommended" is part of the label the agent sent; show it as a badge instead. */
function readChoice(choice: string): { label: string; recommended: boolean } {
  // String checks, not a regex: the label is agent text of any length.
  const trimmed = choice.trimEnd();
  if (!trimmed.toLowerCase().endsWith(RECOMMENDED_SUFFIX))
    return { label: choice, recommended: false };
  const label = trimmed.slice(0, -RECOMMENDED_SUFFIX.length).trimEnd();
  return { label: label || choice, recommended: true };
}

/** "12s", "3m", "1h 5m". */
export function formatWaiting(totalSeconds: number): string {
  if (totalSeconds < SECONDS_PER_MINUTE) return `${totalSeconds}s`;
  const totalMinutes = Math.floor(totalSeconds / SECONDS_PER_MINUTE);
  if (totalMinutes < MINUTES_PER_HOUR) return `${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / MINUTES_PER_HOUR);
  return `${hours}h ${totalMinutes % MINUTES_PER_HOUR}m`;
}

/** Seconds since `receivedAt`, ticking while mounted; null when the request carries no start. */
function useWaitingSeconds(receivedAt: string | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!receivedAt) return;
    const timer = setInterval(() => setNow(Date.now()), WAITING_TICK_MS);
    return () => clearInterval(timer);
  }, [receivedAt]);
  if (!receivedAt) return null;
  const started = Date.parse(receivedAt);
  if (Number.isNaN(started)) return null;
  return Math.max(0, Math.floor((now - started) / MS_PER_SECOND));
}

export interface QuestionBandProps {
  request: InputRequest;
  onRespond: (requestId: string, values: (string | InputAnswer)[]) => void;
  /** Remove a card no client can answer (read-only cards only). */
  onDismiss?: (requestId: string) => void;
}

/**
 * The agent is waiting on the operator: one tab per question, the answer for each, and a
 * review on the last question before the structured answer goes to Skuld. A question no
 * client can answer is shown read-only with where to answer it.
 */
export function QuestionBand({ request, onRespond, onDismiss }: QuestionBandProps) {
  const baseId = useId();
  const total = request.questions.length;
  const [step, setStep] = useState(0);
  const [minimised, setMinimised] = useState(false);
  const [selected, setSelected] = useState<number[][]>(() => request.questions.map(() => []));
  const [text, setText] = useState(() => request.questions.map(() => ''));
  const [other, setOther] = useState(() => request.questions.map(() => false));
  const waitingSeconds = useWaitingSeconds(request.receivedAt);

  if (request.answerable === false) {
    return (
      <section
        className="niuu-chat-request"
        data-answerable="false"
        aria-label="The agent needs your input"
      >
        <div className="niuu-chat-request-copy">
          <strong className="niuu-chat-request-title">Answer in the session terminal</strong>
          {request.questions.map((question, index) => (
            <p className="niuu-chat-request-prompt" key={`${request.requestId}-${index}`}>
              {question.prompt}
              {question.choices.length > 0 && ` (${question.choices.join(' / ')})`}
            </p>
          ))}
          {request.error && (
            <p role="alert" className="niuu-chat-request-error">
              {request.error}
            </p>
          )}
          <p className="niuu-chat-request-prompt">
            This question can&apos;t be answered from here. Answer it in the session&apos;s
            terminal; the card closes when Claude continues.
          </p>
        </div>
        {onDismiss && (
          <button
            type="button"
            className="niuu-chat-request-button"
            onClick={() => onDismiss(request.requestId)}
          >
            Dismiss
          </button>
        )}
      </section>
    );
  }

  // One structured answer per question: the chosen labels (several for a multi-select
  // question) plus any typed text, in the shape Skuld's ask_user_answer accepts.
  const answers: InputAnswer[] = request.questions.map((question, index) => {
    const indexes = selected[index] ?? [];
    const typing = question.choices.length === 0 || other[index];
    const custom = typing ? (text[index] ?? '').trim() : '';
    const values = indexes.map((i) => question.choices[i]!).concat(custom ? [custom] : []);
    return {
      question: question.prompt,
      ...(question.id ? { question_id: question.id } : {}),
      ...(question.header ? { header: question.header } : {}),
      answer: question.multiSelect ? values : (values[0] ?? ''),
      option_indexes: indexes,
      ...(custom ? { free_text: custom } : {}),
    };
  });
  const isAnswered = (index: number) => (answers[index]?.answer.length ?? 0) > 0;
  const complete = answers.every((_, index) => isAnswered(index));
  const isLast = step === total - 1;
  const question = request.questions[step]!;
  const waiting = waitingSeconds === null ? null : formatWaiting(waitingSeconds);

  const choose = (choiceIndex: number) => {
    setSelected((current) =>
      current.map((value, i) => {
        if (i !== step) return value;
        if (!question.multiSelect) return [choiceIndex];
        return value.includes(choiceIndex)
          ? value.filter((v) => v !== choiceIndex)
          : [...value, choiceIndex];
      }),
    );
    if (question.multiSelect) return;
    setOther((current) => current.map((value, i) => (i === step ? false : value)));
    setText((current) => current.map((value, i) => (i === step ? '' : value)));
  };

  const toggleOther = () => {
    const opening = !other[step];
    setOther((current) => current.map((value, i) => (i === step ? opening : value)));
    if (opening && !question.multiSelect) {
      setSelected((current) => current.map((value, i) => (i === step ? [] : value)));
    }
    if (!opening) setText((current) => current.map((value, i) => (i === step ? '' : value)));
  };

  const type = (value: string) => {
    setText((current) => current.map((entry, i) => (i === step ? value : entry)));
    if (question.multiSelect) return;
    setSelected((current) => current.map((entry, i) => (i === step ? [] : entry)));
    setOther((current) => current.map((entry, i) => (i === step ? true : entry)));
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!isLast) {
      if (isAnswered(step)) setStep(step + 1);
      return;
    }
    if (!complete) return;
    onRespond(request.requestId, answers);
  };

  if (minimised) {
    return (
      <section className="niuu-question-band niuu-question-band--minimised">
        <button
          type="button"
          className="niuu-question-band__restore"
          onClick={() => setMinimised(false)}
        >
          <CircleHelp className="niuu-question-band__icon" aria-hidden="true" />
          <span>
            {total} {total === 1 ? 'question' : 'questions'} waiting · Show
          </span>
        </button>
      </section>
    );
  }

  const promptId = `${baseId}-prompt`;
  const panelId = `${baseId}-panel`;
  const typing = question.choices.length === 0 || other[step];

  return (
    <section className="niuu-question-band" aria-label="The agent needs your input">
      <header className="niuu-question-band__head">
        <CircleHelp className="niuu-question-band__icon" aria-hidden="true" />
        <span className="niuu-question-band__title">The agent needs your input</span>
        {waiting && <span className="niuu-question-band__waiting">· waiting {waiting}</span>}
        <button
          type="button"
          className="niuu-question-band__ghost"
          onClick={() => setMinimised(true)}
        >
          Minimise
        </button>
      </header>

      {total > 1 && (
        <div className="niuu-question-band__tabs" role="tablist" aria-label="Questions">
          {request.questions.map((entry, index) => (
            <button
              key={`${baseId}-tab-${index}`}
              type="button"
              role="tab"
              aria-selected={index === step}
              aria-controls={panelId}
              className="niuu-question-band__tab"
              onClick={() => setStep(index)}
            >
              {index + 1}. {entry.header ?? 'Question'}
              {isAnswered(index) && (
                <Check className="niuu-question-band__tab-check" aria-label="answered" />
              )}
            </button>
          ))}
        </div>
      )}

      <form
        id={panelId}
        role={total > 1 ? 'tabpanel' : undefined}
        className="niuu-question-band__panel"
        onSubmit={submit}
      >
        <p id={promptId} className="niuu-question-band__text">
          {question.prompt}
        </p>
        {question.multiSelect && (
          <small className="niuu-chat-request-hint">Select all that apply.</small>
        )}

        {question.choices.length > 0 && (
          <div className="niuu-question-band__options" aria-label="Suggested responses">
            {question.choices.map((choice, choiceIndex) => {
              const { label, recommended } = readChoice(choice);
              const description = question.descriptions?.[choiceIndex];
              const descriptionId = `${baseId}-${step}-${choiceIndex}`;
              return (
                <button
                  type="button"
                  key={choiceIndex}
                  className="niuu-question-band__option"
                  data-multi={question.multiSelect || undefined}
                  aria-label={label}
                  aria-describedby={description ? descriptionId : undefined}
                  aria-pressed={(selected[step] ?? []).includes(choiceIndex)}
                  onClick={() => choose(choiceIndex)}
                >
                  <span className="niuu-question-band__mark" aria-hidden="true" />
                  <span className="niuu-question-band__option-copy">
                    <span className="niuu-question-band__option-label">
                      {label}
                      {recommended && <span className="niuu-question-band__rec">Recommended</span>}
                    </span>
                    {description && (
                      <small id={descriptionId} className="niuu-question-band__option-desc">
                        {description}
                      </small>
                    )}
                  </span>
                </button>
              );
            })}
            <button
              type="button"
              className="niuu-question-band__option"
              data-multi={question.multiSelect || undefined}
              aria-pressed={other[step]}
              onClick={toggleOther}
            >
              <span className="niuu-question-band__mark" aria-hidden="true" />
              <span className="niuu-question-band__option-copy">
                <span className="niuu-question-band__option-label">Something else…</span>
              </span>
            </button>
          </div>
        )}

        {typing && (
          <input
            className="niuu-chat-request-input niuu-question-band__input"
            aria-labelledby={promptId}
            value={text[step] ?? ''}
            onChange={(event) => type(event.target.value)}
            placeholder={question.choices.length ? 'Type another answer' : 'Type a response'}
          />
        )}

        {isLast && total > 1 && (
          <ul className="niuu-question-band__review" aria-label="Your answers">
            {request.questions.map((entry, index) => {
              const answer = answers[index]!.answer;
              return (
                <li key={`${baseId}-review-${index}`}>
                  <span className="niuu-question-band__review-q">
                    {index + 1}. {entry.header ?? entry.prompt}
                  </span>
                  <span
                    className={cn(
                      'niuu-question-band__review-a',
                      !isAnswered(index) && 'niuu-question-band__review-a--missing',
                    )}
                  >
                    {isAnswered(index)
                      ? Array.isArray(answer)
                        ? answer.join(', ')
                        : answer
                      : 'Not answered'}
                  </span>
                </li>
              );
            })}
          </ul>
        )}

        {request.error && (
          <p role="alert" className="niuu-chat-request-error">
            {request.error}
          </p>
        )}

        <div className="niuu-question-band__foot">
          {!isLast && (
            <button
              type="button"
              className="niuu-question-band__ghost"
              onClick={() => setStep(step + 1)}
            >
              Skip
            </button>
          )}
          {isLast ? (
            <button type="submit" className="niuu-question-band__primary" disabled={!complete}>
              Submit
            </button>
          ) : (
            <button
              type="submit"
              className="niuu-question-band__primary"
              disabled={!isAnswered(step)}
            >
              Next
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
