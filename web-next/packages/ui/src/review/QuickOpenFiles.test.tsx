import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QuickOpenFiles, fuzzyScore } from './QuickOpenFiles';

const paths = [
  '/workspace/src/niuu/domain/services/token_scope.py',
  '/workspace/tests/test_niuu/test_token_scope.py',
  '/workspace/scope/README.md',
  '/workspace/Makefile',
];

describe('fuzzyScore', () => {
  it('matches a subsequence, favouring runs and the file name', () => {
    expect(fuzzyScore('/a/b.ts', '')).toBe(0);
    expect(fuzzyScore('/a/b.ts', 'zz')).toBe(-1);
    expect(fuzzyScore(paths[0]!, 'scope')).toBeGreaterThan(fuzzyScore(paths[2]!, 'scope'));
  });
});

describe('QuickOpenFiles', () => {
  it('lists the touched files first, filters as you type and picks with the keyboard', () => {
    const onPick = vi.fn();
    render(
      <QuickOpenFiles
        open
        onOpenChange={vi.fn()}
        candidates={[paths[1]!, ...paths]}
        touched={[paths[1]!]}
        onPick={onPick}
      />,
    );
    const list = screen.getByRole('listbox', { name: 'Files' });
    const options = within(list).getAllByRole('option');
    expect(options).toHaveLength(4);
    expect(options[0]).toHaveTextContent('test_token_scope.py');
    expect(within(options[0]!).getByTitle('The agent touched this file')).toBeInTheDocument();

    const input = screen.getByLabelText('Find a file');
    fireEvent.change(input, { target: { value: 'scope' } });
    expect(within(list).queryByText('Makefile')).not.toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(paths).toContain(onPick.mock.calls[0]![0]);
  });

  it('says when nothing matches and forgets the query when closed', () => {
    const onOpenChange = vi.fn();
    render(<QuickOpenFiles open onOpenChange={onOpenChange} candidates={paths} onPick={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Find a file'), { target: { value: 'zzz' } });
    expect(screen.getByText('No file matches “zzz”.')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText('Find a file'), { key: 'Enter' });
    fireEvent.keyDown(screen.getByLabelText('Find a file'), { key: 'Escape' });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
