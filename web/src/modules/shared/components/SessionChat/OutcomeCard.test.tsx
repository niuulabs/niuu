import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { OutcomeCard, parseOutcomeFields } from './OutcomeCard';

/* ================================================================== */
/*  parseOutcomeFields                                                  */
/* ================================================================== */

describe('parseOutcomeFields', () => {
  it('parses multi-line YAML into key-value pairs', () => {
    const raw = 'verdict: approve\ntests_passing: true\nsummary: all good';
    const fields = parseOutcomeFields(raw);

    expect(fields['verdict']).toBe('approve');
    expect(fields['tests_passing']).toBe('true');
    expect(fields['summary']).toBe('all good');
  });

  it('parses single-line YAML via field pattern', () => {
    const raw = 'verdict: pass summary: looks good';
    const fields = parseOutcomeFields(raw);

    expect(fields['verdict']).toBe('pass');
    expect(fields['summary']).toBe('looks good');
  });

  it('ignores comment lines starting with #', () => {
    const raw = '# this is a comment\nverdict: retry';
    const fields = parseOutcomeFields(raw);

    expect(fields['verdict']).toBe('retry');
    expect(Object.keys(fields)).not.toContain('#');
  });

  it('returns an empty object for empty input', () => {
    expect(parseOutcomeFields('')).toEqual({});
  });

  it('handles colons in values correctly', () => {
    const raw = 'verdict: approve\nsummary: result: good';
    const fields = parseOutcomeFields(raw);

    expect(fields['verdict']).toBe('approve');
    // Only takes up to first colon for the key; rest is value
    expect(fields['summary']).toBe('result: good');
  });
});

/* ================================================================== */
/*  OutcomeCard                                                         */
/* ================================================================== */

describe('OutcomeCard', () => {
  const yaml = `verdict: approve
tests_passing: true
scope_adherence: 0.95
summary: Implementation complete with full test coverage`;

  it('renders the "Outcome" label', () => {
    render(<OutcomeCard yaml={yaml} />);
    expect(screen.getByText('Outcome')).toBeInTheDocument();
  });

  it('renders the verdict as a badge', () => {
    render(<OutcomeCard yaml={yaml} />);
    expect(screen.getByText('approve')).toBeInTheDocument();
  });

  it('sets data-verdict="approve" on the badge', () => {
    const { container } = render(<OutcomeCard yaml={yaml} />);
    const badge = container.querySelector('[class*="outcomeBadge"]');
    expect(badge).not.toBeNull();
    expect(badge).toHaveAttribute('data-verdict', 'approve');
  });

  it('sets data-verdict="pass" for pass verdict', () => {
    const { container } = render(<OutcomeCard yaml={'verdict: pass\nsummary: ok'} />);
    const badge = container.querySelector('[class*="outcomeBadge"]');
    expect(badge).toHaveAttribute('data-verdict', 'pass');
  });

  it('sets data-verdict="retry" for retry verdict', () => {
    const { container } = render(<OutcomeCard yaml={'verdict: retry\nsummary: needs more work'} />);
    const badge = container.querySelector('[class*="outcomeBadge"]');
    expect(badge).toHaveAttribute('data-verdict', 'retry');
  });

  it('sets data-verdict="escalate" for escalate verdict', () => {
    const { container } = render(
      <OutcomeCard yaml={'verdict: escalate\nsummary: requires escalation'} />
    );
    const badge = container.querySelector('[class*="outcomeBadge"]');
    expect(badge).toHaveAttribute('data-verdict', 'escalate');
  });

  it('sets data-verdict="fail" for fail verdict', () => {
    const { container } = render(<OutcomeCard yaml={'verdict: fail\nsummary: failed'} />);
    const badge = container.querySelector('[class*="outcomeBadge"]');
    expect(badge).toHaveAttribute('data-verdict', 'fail');
  });

  it('sets data-verdict="unknown" for unrecognized verdicts', () => {
    const { container } = render(<OutcomeCard yaml={'verdict: custom_status\nsummary: unusual'} />);
    const badge = container.querySelector('[class*="outcomeBadge"]');
    expect(badge).toHaveAttribute('data-verdict', 'unknown');
  });

  it('renders non-verdict fields as key-value pairs', () => {
    render(<OutcomeCard yaml={yaml} />);

    expect(screen.getByText('tests_passing')).toBeInTheDocument();
    expect(screen.getByText('true')).toBeInTheDocument();
    expect(screen.getByText('scope_adherence')).toBeInTheDocument();
    expect(screen.getByText('0.95')).toBeInTheDocument();
    expect(screen.getByText('summary')).toBeInTheDocument();
    expect(screen.getByText('Implementation complete with full test coverage')).toBeInTheDocument();
  });

  it('does not render verdict as a field row (only as badge)', () => {
    render(<OutcomeCard yaml={yaml} />);

    // 'approve' should appear only once (as the badge), not as a field value
    const approveElements = screen.getAllByText('approve');
    expect(approveElements).toHaveLength(1);
  });

  it('does not render a badge when verdict is absent', () => {
    const { container } = render(<OutcomeCard yaml="summary: analysis complete" />);
    expect(container.querySelector('[class*="outcomeBadge"]')).toBeNull();
  });

  it('renders "Outcome" label even when no verdict', () => {
    render(<OutcomeCard yaml="summary: analysis complete" />);
    expect(screen.getByText('Outcome')).toBeInTheDocument();
    expect(screen.getByText('analysis complete')).toBeInTheDocument();
  });

  it('shows "Show raw" button initially', () => {
    render(<OutcomeCard yaml={yaml} />);

    expect(screen.getByText('Show raw')).toBeInTheDocument();
    expect(screen.queryByText('Hide raw')).toBeNull();
  });

  it('shows raw YAML when "Show raw" is clicked', async () => {
    render(<OutcomeCard yaml={yaml} />);

    await act(async () => {
      fireEvent.click(screen.getByText('Show raw'));
    });

    expect(screen.getByText('Hide raw')).toBeInTheDocument();
    const pre = screen.getByText(/verdict: approve/);
    expect(pre.tagName).toBe('PRE');
  });

  it('hides raw YAML when "Hide raw" is clicked', async () => {
    render(<OutcomeCard yaml={yaml} />);

    await act(async () => {
      fireEvent.click(screen.getByText('Show raw'));
    });

    await act(async () => {
      fireEvent.click(screen.getByText('Hide raw'));
    });

    expect(screen.getByText('Show raw')).toBeInTheDocument();
    expect(screen.queryByText(/verdict: approve/)).toBeNull();
  });

  it('raw YAML is trimmed before display', async () => {
    const paddedYaml = '\n  verdict: approve\n  summary: ok\n  ';
    render(<OutcomeCard yaml={paddedYaml} />);

    await act(async () => {
      fireEvent.click(screen.getByText('Show raw'));
    });

    const pre = screen.getByText(/verdict: approve/);
    expect(pre.textContent?.startsWith('\n')).toBe(false);
    expect(pre.textContent?.endsWith('\n  ')).toBe(false);
  });
});
