import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { TagChipInput } from './TagChipInput';

function Harness({
  initial = [],
  onChange,
}: {
  initial?: string[];
  onChange?: (tags: string[]) => void;
}) {
  const [tags, setTags] = useState<string[]>(initial);
  return (
    <TagChipInput
      label="Host tags"
      value={tags}
      suggestions={['gpu', 'fast-disk']}
      onChange={(next) => {
        setTags(next);
        onChange?.(next);
      }}
    >
      <span>extra control</span>
    </TagChipInput>
  );
}

describe('TagChipInput', () => {
  it('adds typed tags on Enter and on comma, without repeats', () => {
    const onChange = vi.fn();
    render(<Harness initial={['gpu']} onChange={onChange} />);
    const input = screen.getByLabelText('Add to Host tags');
    fireEvent.change(input, { target: { value: 'site:hnoss' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenLastCalledWith(['gpu', 'site:hnoss']);
    fireEvent.change(input, { target: { value: 'gpu, arm64' } });
    fireEvent.keyDown(input, { key: ',' });
    expect(onChange).toHaveBeenLastCalledWith(['gpu', 'site:hnoss', 'arm64']);
    expect(input).toHaveValue('');
  });

  it('ignores an empty entry', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.keyDown(screen.getByLabelText('Add to Host tags'), { key: 'Enter' });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('keeps what was typed when the input loses focus', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const input = screen.getByLabelText('Add to Host tags');
    fireEvent.change(input, { target: { value: 'arm64' } });
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledWith(['arm64']);
  });

  it('removes a tag with its cross', () => {
    const onChange = vi.fn();
    render(<Harness initial={['gpu', 'arm64']} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Remove tag gpu' }));
    expect(onChange).toHaveBeenCalledWith(['arm64']);
  });

  it('removes the last tag with Backspace on an empty input only', () => {
    const onChange = vi.fn();
    render(<Harness initial={['gpu', 'arm64']} onChange={onChange} />);
    const input = screen.getByLabelText('Add to Host tags');
    fireEvent.change(input, { target: { value: 'x' } });
    fireEvent.keyDown(input, { key: 'Backspace' });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.keyDown(input, { key: 'Backspace' });
    expect(onChange).toHaveBeenCalledWith(['gpu']);
  });

  it('does nothing on Backspace when there is no tag', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.keyDown(screen.getByLabelText('Add to Host tags'), { key: 'Backspace' });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('offers the tags it does not hold yet and shows its extra controls', () => {
    const { container } = render(<Harness initial={['gpu']} />);
    const options = [...container.querySelectorAll('datalist option')].map((o) =>
      o.getAttribute('value'),
    );
    expect(options).toEqual(['fast-disk']);
    expect(screen.getByText('extra control')).toBeInTheDocument();
  });

  it('has no suggestion list without suggestions, and can be disabled', () => {
    const { container } = render(
      <TagChipInput label="Tags" value={['a']} onChange={vi.fn()} disabled />,
    );
    expect(container.querySelector('datalist')).toBeNull();
    expect(screen.getByLabelText('Add to Tags')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Remove tag a' })).toBeDisabled();
  });
});
