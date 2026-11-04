import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { OutcomeCard, extractOutcomeBlock } from './OutcomeCard';
import { MarkdownContent } from '../MarkdownContent';

describe('OutcomeCard', () => {
  const raw = 'verdict: pass\nsummary: All checks passed\ndetails: 5/5';

  it('renders with verdict badge', () => {
    render(<OutcomeCard raw={raw} />);
    expect(screen.getByText('pass')).toBeInTheDocument();
    expect(screen.getByText('All checks passed')).toBeInTheDocument();
  });

  it('shows raw toggle', () => {
    const { container } = render(<OutcomeCard raw={raw} />);
    fireEvent.click(screen.getByText('Show raw'));
    expect(screen.getByText('Hide raw')).toBeInTheDocument();
    const preEl = container.querySelector('pre.niuu-chat-outcome-raw');
    expect(preEl).not.toBeNull();
    expect(preEl!.textContent).toContain('verdict: pass');
  });

  it('renders without verdict gracefully', () => {
    render(<OutcomeCard raw="summary: No verdict here" />);
    expect(screen.getByText('Outcome')).toBeInTheDocument();
  });

  it('applies verdict class for fail', () => {
    const { container } = render(<OutcomeCard raw={'verdict: fail\nsummary: failed'} />);
    expect(container.querySelector('[data-testid="outcome-card"]')).toHaveClass(
      'niuu-chat-outcome--fail',
    );
  });

  it('renders markdown inside summary and field values', () => {
    render(
      <OutcomeCard
        raw={'verdict: pass\nsummary: See [docs](https://example.com)\ndetails: Use `npm test`'}
      />,
    );
    const link = screen.getByRole('button', { name: 'docs' });
    expect(link).toHaveClass('niuu-chat-md-link');
    expect(screen.getByText('npm test')).toBeInTheDocument();
  });

  it('renders multiline block scalar fields from raw outcome yaml', () => {
    render(
      <OutcomeCard
        raw={`verdict: conditional
summary: Top level summary
findings: |
  ## Verified
  - **Route pair count**: 26
  - Use \`/api/v1/credentials/secrets\`
`}
      />,
    );

    expect(screen.getByText('Verified')).toBeInTheDocument();
    const routePairItem = screen.getByText('Route pair count').closest('li');
    expect(routePairItem).not.toBeNull();
    expect(routePairItem).toHaveTextContent('Route pair count: 26');
    expect(screen.getByText('/api/v1/credentials/secrets')).toBeInTheDocument();
  });

  it('normalizes soft-wrapped outcome fields before rendering', () => {
    render(
      <OutcomeCard
        raw={`ver
dict
:
 opinion
_sub
mitted

summary
:
 W
rote
 opinion
 recommending
 risk
-tier
ed
 opt
-out
 autonomy
.

page
_path
:
 council
/
demo
/
opinion
-b
.md`}
      />,
    );

    expect(screen.getByText('opinion_submitted')).toBeInTheDocument();
    expect(
      screen.getByText('Wrote opinion recommending risk-tiered opt-out autonomy.'),
    ).toBeInTheDocument();
    expect(screen.getByText('council/demo/opinion-b.md')).toBeInTheDocument();
  });

  it('preserves spaces after soft-wrapped punctuation in summaries', () => {
    render(
      <OutcomeCard
        raw={`ver
dict
:
 opinion
_sub
mitted

summary
:
 W
rote
 opinion
 recommending
 risk
-tier
ed
 opt
-out
 autonomy
 with
 human
 approval
 for
 low
-confidence
,
 high
-risk
,
 or
 bootstrap
/test
 cases
.`}
      />,
    );

    expect(
      screen.getByText(
        'Wrote opinion recommending risk-tiered opt-out autonomy with human approval for low-confidence, high-risk, or bootstrap/test cases.',
      ),
    ).toBeInTheDocument();
  });

  it('restores inline label bullets without collapsing the label text', () => {
    render(
      <OutcomeCard
        raw={
          'verdict: pass\nsummary: Recommendations: - Keep manual approval for risky commands - Auto-approve routine reads'
        }
      />,
    );

    expect(screen.getByText('Recommendations:')).toBeInTheDocument();
    expect(screen.getByText('Keep manual approval for risky commands')).toBeInTheDocument();
    expect(screen.getByText('Auto-approve routine reads')).toBeInTheDocument();
  });

  it('leaves lowercase labels inline when they are not intended as markdown sections', () => {
    render(<OutcomeCard raw={'verdict: pass\nsummary: notes: - keep lowercase inline'} />);

    expect(screen.getByText('notes: - keep lowercase inline')).toBeInTheDocument();
  });

  it('breaks heading lines that include an inline lowercase bullet payload', () => {
    render(<OutcomeCard raw={'verdict: pass\nsummary: ## Heading - item'} />);

    expect(screen.getByRole('heading', { name: 'Heading' })).toBeInTheDocument();
    expect(screen.getByText('item')).toBeInTheDocument();
  });
});

describe('extractOutcomeBlock', () => {
  it('extracts outcome from backtick block', () => {
    const text = 'before\n```outcome\nverdict: pass\n```\nafter';
    const result = extractOutcomeBlock(text);
    expect(result).not.toBeNull();
    expect(result!.raw).toBe('verdict: pass');
    expect(result!.before).toContain('before');
    expect(result!.after).toContain('after');
  });

  it('returns null for text without outcome block', () => {
    expect(extractOutcomeBlock('just normal text')).toBeNull();
  });

  it('extracts outcome from dashed markers', () => {
    const text = 'before\n---outcome---\nverdict: pass\nsummary: All good\n---end---\nafter';
    const result = extractOutcomeBlock(text);
    expect(result).toEqual({
      before: 'before\n',
      raw: 'verdict: pass\nsummary: All good',
      after: '\nafter',
    });
  });

  it('extracts outcome from dashed markers closed by bare dashes', () => {
    const text = 'before\n---outcome---\nverdict: pass\nsummary: All good\n---\nafter';
    const result = extractOutcomeBlock(text);
    expect(result).toEqual({
      before: 'before\n',
      raw: 'verdict: pass\nsummary: All good',
      after: '\nafter',
    });
  });

  it('extracts and normalizes soft-wrapped dashed outcome markers', () => {
    const text = `before
---
out
come
---

ver
dict
:
 review
_sub
mitted

summary
:
 Prefer
 Opinion
 A
.

page
_path
:
 council
/
demo
/
review
-b
.md

---
end
---
after`;
    const result = extractOutcomeBlock(text);
    expect(result).toEqual({
      before: 'before\n',
      raw: 'verdict: review_submitted\nsummary: Prefer Opinion A.\npage_path: council/demo/review-b.md',
      after: '\nafter',
    });
  });
});

describe('a JSON outcome, as a coordinator writes it', () => {
  const coordinator =
    '---outcome---\n{"working_state":{"objectives":["Run Ticket Planning Flow for NIU-1117."],"observations":["Started task a2a-1."],"hypotheses":[],"unknowns":["The gate is pending."]},"selected_next_action":"Await the workflow update.","next_action_timing":"external_event"}';

  it('is found with no closing marker, since the object closes it', () => {
    const block = extractOutcomeBlock(coordinator);
    expect(block?.raw.startsWith('{"working_state"')).toBe(true);
    expect(block?.after).toBe('');
    expect(extractOutcomeBlock('---outcome---\nnot json and never closed')).toBeNull();
  });

  it('reads as fields: nested state read through, lists as bullets, empty ones left out', () => {
    render(<OutcomeCard raw={extractOutcomeBlock(coordinator)!.raw} />);
    expect(screen.getByText('objectives')).toBeInTheDocument();
    expect(screen.getByText('Run Ticket Planning Flow for NIU-1117.')).toBeInTheDocument();
    expect(screen.getByText('selected next action')).toBeInTheDocument();
    expect(screen.getByText('Await the workflow update.')).toBeInTheDocument();
    expect(screen.queryByText('hypotheses')).toBeNull();
    expect(screen.queryByText(/working_state/)).toBeNull();
  });

  const state =
    '{"working_state":{"observations":["Plan --- approved at the gate."]},"selected_next_action":"Wait for the operator.","next_action_timing":"operator_input"}';

  it('keeps a dash run inside a JSON string as part of the outcome', () => {
    const block = extractOutcomeBlock(`Plan review is pending.\n\n---outcome---\n${state}`);
    expect(block).toEqual({ before: 'Plan review is pending.\n\n', raw: state, after: '' });
  });

  it('ends at the object, so prose after it stays prose', () => {
    const block = extractOutcomeBlock(`---outcome---\n${state}\n\nReply on the Review tab.`);
    expect(block?.raw).toBe(state);
    expect(block?.after).toBe('\n\nReply on the Review tab.');
  });

  it('takes an end marker after the object as its close', () => {
    const block = extractOutcomeBlock(`---outcome---\n${state}\n---end---\nAfter.`);
    expect(block?.raw).toBe(state);
    expect(block?.after).toBe('\nAfter.');
  });

  it('renders as a card in a message, with none of the object left as text', () => {
    render(<MarkdownContent content={`Plan review is pending.\n\n---outcome---\n${state}`} />);
    expect(screen.getByTestId('outcome-card')).toBeInTheDocument();
    expect(screen.getByText('Plan --- approved at the gate.')).toBeInTheDocument();
    expect(screen.queryByText(/---outcome---|next_action_timing|"\]\}/)).toBeNull();
  });
});
