import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { CopyButton } from './CopyButton';

function stubClipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
}

afterEach(() => {
  Reflect.deleteProperty(navigator, 'clipboard');
});

describe('CopyButton', () => {
  it('copies the text and says so', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);
    render(<CopyButton text="sudo sh" label="Copy command" primary />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy command' }));
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith('sudo sh');
  });

  it('shows the error when the clipboard refuses', async () => {
    stubClipboard(vi.fn().mockRejectedValue(new Error('permission denied')));
    render(<CopyButton text="x" />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not copy: permission denied');
    expect(screen.getByRole('button', { name: 'Copy' })).toBeInTheDocument();
  });

  it('shows the error when there is no clipboard at all', async () => {
    render(<CopyButton text="x" />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not copy');
  });
});
