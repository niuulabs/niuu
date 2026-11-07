import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { QuestionBand, formatWaiting } from './QuestionBand';
import type { InputRequest } from '../../types';

const migration: InputRequest = {
  requestId: 'req-1',
  receivedAt: '2026-11-05T10:00:00.000Z',
  questions: [
    {
      prompt: 'Which migration set should the column go in?',
      header: 'Migration set',
      choices: ['Ting (Recommended)', 'Volundr', 'Both'],
      descriptions: ['run_steps is a Ting table', 'Next to the run page queries', undefined],
    },
    {
      prompt: 'Backfill existing rows?',
      header: 'Backfill',
      choices: ['No', 'Yes, 30 days'],
    },
  ],
};

function setup(request: InputRequest = migration, onDismiss?: (id: string) => void) {
  const onRespond = vi.fn();
  render(<QuestionBand request={request} onRespond={onRespond} onDismiss={onDismiss} />);
  return { onRespond };
}

afterEach(() => {
  vi.useRealTimers();
});

describe('formatWaiting', () => {
  it('counts seconds, minutes, then hours and minutes', () => {
    expect(formatWaiting(0)).toBe('0s');
    expect(formatWaiting(59)).toBe('59s');
    expect(formatWaiting(60)).toBe('1m');
    expect(formatWaiting(3 * 60 + 30)).toBe('3m');
    expect(formatWaiting(60 * 60)).toBe('1h 0m');
    expect(formatWaiting(2 * 3600 + 5 * 60)).toBe('2h 5m');
  });
});

describe('QuestionBand', () => {
  it('shows the header with a waiting time that ticks', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-05T10:03:00.000Z'));
    setup();
    expect(screen.getByText('The agent needs your input')).toBeInTheDocument();
    expect(screen.getByText('· waiting 3m')).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByText('· waiting 4m')).toBeInTheDocument();
  });

  it('omits the waiting time when the request has no start, or an unreadable one', () => {
    const { unmount } = render(
      <QuestionBand request={{ ...migration, receivedAt: undefined }} onRespond={vi.fn()} />,
    );
    expect(screen.queryByText(/waiting/)).not.toBeInTheDocument();
    unmount();
    render(
      <QuestionBand request={{ ...migration, receivedAt: 'yesterday' }} onRespond={vi.fn()} />,
    );
    expect(screen.queryByText(/waiting/)).not.toBeInTheDocument();
  });

  it('has one tab per question and starts on the first', () => {
    setup();
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual(['1. Migration set', '2. Backfill']);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Which migration set should the column go in?')).toBeInTheDocument();
    fireEvent.click(tabs[1]!);
    expect(tabs[1]).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Backfill existing rows?')).toBeInTheDocument();
  });

  it('shows no tabs for a single question', () => {
    setup({ requestId: 'r', questions: [{ prompt: 'One?', choices: ['A'] }] });
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  });

  it('badges a Recommended choice, strips it from the label, and sends the original text', () => {
    const { onRespond } = setup({
      requestId: 'r',
      questions: [{ prompt: 'Which?', choices: ['Ting (Recommended)', 'Volundr'] }],
    });
    const option = screen.getByRole('button', { name: 'Ting' });
    expect(within(option).getByText('Recommended')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Volundr' })).not.toHaveTextContent('Recommended');
    fireEvent.click(option);
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    expect(onRespond).toHaveBeenCalledWith('r', [
      { question: 'Which?', answer: 'Ting (Recommended)', option_indexes: [0] },
    ]);
  });

  it('keeps a choice that is only "(Recommended)" readable', () => {
    setup({ requestId: 'r', questions: [{ prompt: 'Which?', choices: ['(Recommended)'] }] });
    expect(screen.getByRole('button', { name: '(Recommended)' })).toBeInTheDocument();
  });

  it('ties a description to its choice', () => {
    setup();
    expect(screen.getByRole('button', { name: 'Ting' })).toHaveAccessibleDescription(
      'run_steps is a Ting table',
    );
    expect(screen.getByRole('button', { name: 'Both' })).not.toHaveAttribute('aria-describedby');
  });

  it('answers with something else typed in', () => {
    const { onRespond } = setup({
      requestId: 'r',
      questions: [{ prompt: 'Deliver how?', choices: ['Folio', 'Tar'] }],
    });
    expect(screen.queryByLabelText('Deliver how?')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Folio' }));
    fireEvent.click(screen.getByRole('button', { name: 'Something else…' }));
    expect(screen.getByRole('button', { name: 'Folio' })).toHaveAttribute('aria-pressed', 'false');
    const input = screen.getByLabelText('Deliver how?');
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled();
    fireEvent.change(input, { target: { value: '  Zip  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    expect(onRespond).toHaveBeenCalledWith('r', [
      { question: 'Deliver how?', answer: 'Zip', option_indexes: [], free_text: 'Zip' },
    ]);
  });

  it('typing replaces a chosen option, and choosing one clears the typed text', () => {
    setup({ requestId: 'r', questions: [{ prompt: 'Deliver how?', choices: ['Folio', 'Tar'] }] });
    fireEvent.click(screen.getByRole('button', { name: 'Folio' }));
    fireEvent.click(screen.getByRole('button', { name: 'Something else…' }));
    fireEvent.change(screen.getByLabelText('Deliver how?'), { target: { value: 'Zip' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tar' }));
    expect(screen.queryByLabelText('Deliver how?')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Something else…' }));
    expect(screen.getByLabelText('Deliver how?')).toHaveValue('');
  });

  it('closing "Something else…" drops the typed text', () => {
    const { onRespond } = setup({
      requestId: 'r',
      questions: [{ prompt: 'Deliver how?', choices: ['Folio'] }],
    });
    const other = screen.getByRole('button', { name: 'Something else…' });
    fireEvent.click(other);
    fireEvent.change(screen.getByLabelText('Deliver how?'), { target: { value: 'Zip' } });
    fireEvent.click(other);
    expect(screen.queryByLabelText('Deliver how?')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled();
    expect(onRespond).not.toHaveBeenCalled();
  });

  it('asks a question without choices as free text, trimmed, and rejects blank', () => {
    const { onRespond } = setup({
      requestId: 'r',
      questions: [{ prompt: 'Name the release', choices: [] }],
    });
    const input = screen.getByLabelText('Name the release');
    const submit = screen.getByRole('button', { name: 'Submit' });
    expect(submit).toBeDisabled();
    fireEvent.change(input, { target: { value: '   ' } });
    expect(submit).toBeDisabled();
    fireEvent.change(input, { target: { value: ' July ' } });
    fireEvent.submit(input.closest('form')!);
    expect(onRespond).toHaveBeenCalledWith('r', [
      { question: 'Name the release', answer: 'July', option_indexes: [], free_text: 'July' },
    ]);
  });

  it('answers a multi-select question with every chosen option plus typed text', () => {
    const { onRespond } = setup({
      requestId: 'r',
      questions: [
        {
          prompt: 'Which platforms?',
          choices: ['iOS', 'macOS', 'watchOS'],
          multiSelect: true,
          id: 'platforms',
          header: 'Targets',
        },
      ],
    });
    expect(screen.getByText('Select all that apply.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'iOS' }));
    fireEvent.click(screen.getByRole('button', { name: 'watchOS' }));
    fireEvent.click(screen.getByRole('button', { name: 'macOS' }));
    fireEvent.click(screen.getByRole('button', { name: 'macOS' }));
    expect(screen.getByRole('button', { name: 'iOS' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'macOS' })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(screen.getByRole('button', { name: 'Something else…' }));
    fireEvent.change(screen.getByLabelText('Which platforms?'), { target: { value: 'visionOS' } });
    // Typing does not drop the toggled choices in a multi-select question.
    expect(screen.getByRole('button', { name: 'iOS' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    expect(onRespond).toHaveBeenCalledWith('r', [
      {
        question: 'Which platforms?',
        question_id: 'platforms',
        header: 'Targets',
        answer: ['iOS', 'watchOS', 'visionOS'],
        option_indexes: [0, 2],
        free_text: 'visionOS',
      },
    ]);
  });

  it('walks the questions with Next, then reviews and submits on the last', () => {
    const { onRespond } = setup();
    const next = screen.getByRole('button', { name: 'Next' });
    expect(next).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Submit' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Ting' }));
    expect(next).toBeEnabled();
    fireEvent.click(next);

    expect(screen.getByText('Backfill existing rows?')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Skip' })).not.toBeInTheDocument();
    const review = screen.getByRole('list', { name: 'Your answers' });
    expect(within(review).getByText('Ting (Recommended)')).toBeInTheDocument();
    expect(within(review).getByText('Not answered')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'No' }));
    expect(within(review).queryByText('Not answered')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    expect(onRespond).toHaveBeenCalledWith('req-1', [
      {
        question: 'Which migration set should the column go in?',
        header: 'Migration set',
        answer: 'Ting (Recommended)',
        option_indexes: [0],
      },
      {
        question: 'Backfill existing rows?',
        header: 'Backfill',
        answer: 'No',
        option_indexes: [0],
      },
    ]);
  });

  it('Skip moves on without an answer and Submit stays blocked until every question is answered', () => {
    const { onRespond } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
    fireEvent.click(screen.getByRole('button', { name: 'Yes, 30 days' }));
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled();
    fireEvent.submit(screen.getByRole('button', { name: 'Submit' }).closest('form')!);
    expect(onRespond).not.toHaveBeenCalled();

    fireEvent.click(screen.getAllByRole('tab')[0]!);
    fireEvent.click(screen.getByRole('button', { name: 'Volundr' }));
    fireEvent.click(screen.getAllByRole('tab')[1]!);
    expect(screen.getByRole('button', { name: 'Submit' })).toBeEnabled();
  });

  it('Enter in the text box advances an answered question instead of submitting', () => {
    const { onRespond } = setup({
      requestId: 'r',
      questions: [
        { prompt: 'First?', choices: [] },
        { prompt: 'Second?', choices: [] },
      ],
    });
    const input = screen.getByLabelText('First?');
    fireEvent.submit(input.closest('form')!);
    expect(screen.getByLabelText('First?')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: 'one' } });
    fireEvent.submit(input.closest('form')!);
    expect(screen.getByLabelText('Second?')).toBeInTheDocument();
    expect(onRespond).not.toHaveBeenCalled();
  });

  it('minimises to a count and restores with its choices intact', () => {
    setup();
    fireEvent.click(screen.getByRole('button', { name: 'Ting' }));
    fireEvent.click(screen.getByRole('button', { name: 'Minimise' }));
    expect(
      screen.queryByText('Which migration set should the column go in?'),
    ).not.toBeInTheDocument();
    const show = screen.getByRole('button', { name: /2 questions waiting · Show/ });
    fireEvent.click(show);
    expect(screen.getByRole('button', { name: 'Ting' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('counts a single waiting question in the singular', () => {
    setup({ requestId: 'r', questions: [{ prompt: 'One?', choices: ['A'] }] });
    fireEvent.click(screen.getByRole('button', { name: 'Minimise' }));
    expect(screen.getByRole('button', { name: /1 question waiting · Show/ })).toBeInTheDocument();
  });

  it('shows the broker refusal on the card', () => {
    setup({
      requestId: 'r',
      questions: [{ prompt: 'Deliver how?', choices: ['Folio'] }],
      error: 'The live Claude menu does not match the pending question state',
    });
    expect(screen.getByRole('alert')).toHaveTextContent('does not match');
  });

  it('shows an unanswerable question read-only, with where to answer it and a dismiss', () => {
    const onDismiss = vi.fn();
    const { onRespond } = setup(
      {
        requestId: 'tty-1',
        questions: [{ prompt: 'Which build flavour?', choices: ['debug', 'release'] }],
        answerable: false,
        error: 'This pending control survived in history',
      },
      onDismiss,
    );
    expect(screen.getByText('Answer in the session terminal')).toBeInTheDocument();
    expect(screen.getByText('Which build flavour? (debug / release)')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('survived in history');
    expect(screen.queryByRole('button', { name: 'Submit' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'debug' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onDismiss).toHaveBeenCalledWith('tty-1');
    expect(onRespond).not.toHaveBeenCalled();
  });

  it('offers no dismiss when the host gave none', () => {
    setup({
      requestId: 'tty-1',
      questions: [{ prompt: 'Which?', choices: [] }],
      answerable: false,
    });
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument();
  });
});
