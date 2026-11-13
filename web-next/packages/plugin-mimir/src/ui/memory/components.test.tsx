import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AskBox } from './AskBox';

describe('AskBox', () => {
  it('replaces the draft when the question in the URL changes', () => {
    const { rerender } = render(<AskBox value="first" onAsk={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Ask memory'), { target: { value: 'typed over it' } });
    rerender(<AskBox value="second" onAsk={vi.fn()} />);
    expect(screen.getByLabelText('Ask memory')).toHaveValue('second');
  });

  it('keeps the draft while the question stays the same', () => {
    const { rerender } = render(<AskBox value="first" onAsk={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Ask memory'), { target: { value: 'still typing' } });
    rerender(<AskBox value="first" onAsk={vi.fn()} />);
    expect(screen.getByLabelText('Ask memory')).toHaveValue('still typing');
  });

  it('trims the question it hands on', () => {
    const onAsk = vi.fn();
    render(<AskBox value="" onAsk={onAsk} />);
    fireEvent.change(screen.getByLabelText('Ask memory'), { target: { value: '  spaced  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask memory' }));
    expect(onAsk).toHaveBeenCalledWith('spaced');
  });

  it('takes a custom placeholder', () => {
    render(<AskBox value="" placeholder="Ask about this realm…" onAsk={vi.fn()} />);
    expect(screen.getByPlaceholderText('Ask about this realm…')).toBeInTheDocument();
  });
});
