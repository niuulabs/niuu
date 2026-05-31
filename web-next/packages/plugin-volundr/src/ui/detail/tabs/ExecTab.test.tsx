import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ExecEntry } from '../../../domain/exec';
import { ExecTab } from './ExecTab';

function makeEntry(overrides: Partial<ExecEntry>): ExecEntry {
  return {
    id: 'exec-1',
    command: 'pwd',
    output: '',
    status: 'success',
    startedAt: 1_000,
    finishedAt: 3_000,
    ...overrides,
  };
}

describe('ExecTab', () => {
  it('renders the empty state and only submits trimmed commands', async () => {
    const run = vi.fn<(...args: [string]) => Promise<void>>().mockResolvedValue();

    render(<ExecTab exec={{ history: [], isRunning: false, run }} />);

    expect(screen.getByTestId('exec-empty')).toHaveTextContent('No commands run yet.');

    const input = screen.getByTestId('exec-input');
    const button = screen.getByTestId('exec-run-btn');

    expect(button).toBeDisabled();

    fireEvent.change(input, { target: { value: '   echo hello   ' } });
    expect(button).not.toBeDisabled();

    fireEvent.click(button);

    expect(run).toHaveBeenCalledWith('echo hello');
    expect(input).toHaveValue('');
  });

  it('does not submit while an exec is already running', () => {
    const run = vi.fn<(...args: [string]) => Promise<void>>().mockResolvedValue();

    render(<ExecTab exec={{ history: [], isRunning: true, run }} />);

    const input = screen.getByTestId('exec-input');
    const button = screen.getByTestId('exec-run-btn');

    expect(input).toBeDisabled();
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent('running…');

    fireEvent.change(input, { target: { value: 'echo blocked' } });
    fireEvent.submit(button.closest('form')!);

    expect(run).not.toHaveBeenCalled();
  });

  it('renders exec history in reverse order with status-specific output', () => {
    const run = vi.fn<(...args: [string]) => Promise<void>>().mockResolvedValue();
    const history = [
      makeEntry({
        id: 'success',
        command: 'git status',
        output: 'clean',
        status: 'success',
      }),
      makeEntry({
        id: 'error',
        command: 'npm test',
        output: 'boom',
        status: 'error',
      }),
      makeEntry({
        id: 'running',
        command: 'tail -f logs',
        output: '',
        status: 'running',
        finishedAt: undefined,
      }),
    ];

    render(<ExecTab exec={{ history, isRunning: false, run }} />);

    const rows = screen.getAllByTestId('exec-entry');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent('tail -f logs');
    expect(rows[1]).toHaveTextContent('npm test');
    expect(rows[2]).toHaveTextContent('git status');

    expect(screen.getByLabelText('status: running')).toHaveTextContent('⟳');
    expect(screen.getByLabelText('status: error')).toHaveTextContent('✗');
    expect(screen.getByLabelText('status: success')).toHaveTextContent('✓');

    expect(screen.getAllByTestId('exec-output')).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('…');
    expect(rows[2]).toHaveTextContent('2.00s');
  });
});
