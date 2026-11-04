import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { GateDecision, type GateDecisionProps } from './GateDecision';

function Harness(props: Partial<GateDecisionProps>) {
  const [note, setNote] = useState('');
  return (
    <GateDecision
      title="Plan review"
      note={note}
      onNoteChange={setNote}
      onApprove={vi.fn()}
      onRequestChanges={vi.fn()}
      {...props}
    />
  );
}

describe('GateDecision', () => {
  it('shows what is being decided', () => {
    render(
      <Harness
        eyebrow="Waiting for you"
        summary="The plan is ready."
        facts={[{ label: 'Reason', value: 'Scope changed.' }]}
        hint="Approve continues to Building."
      />,
    );

    expect(screen.getByRole('region', { name: 'Plan review' })).toBeInTheDocument();
    expect(screen.getByText('Waiting for you')).toBeInTheDocument();
    expect(screen.getByText('The plan is ready.')).toBeInTheDocument();
    expect(screen.getByText('Scope changed.')).toBeInTheDocument();
    expect(screen.getByText('Approve continues to Building.')).toBeInTheDocument();
  });

  it('approves without a note', () => {
    const onApprove = vi.fn();
    render(<Harness onApprove={onApprove} approveLabel="Approve → Merging" />);

    fireEvent.click(screen.getByRole('button', { name: 'Approve → Merging' }));

    expect(onApprove).toHaveBeenCalledOnce();
  });

  it('asks what should change when a note is required', () => {
    const onRequestChanges = vi.fn();
    render(<Harness requireNote onRequestChanges={onRequestChanges} />);

    fireEvent.click(screen.getByRole('button', { name: 'Request changes' }));

    expect(screen.getByRole('alert')).toHaveTextContent('Say what should change first.');
    expect(onRequestChanges).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Your note'), { target: { value: 'Add a test.' } });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Request changes' }));
    expect(onRequestChanges).toHaveBeenCalledOnce();
  });

  it('counts line comments as saying what should change', () => {
    const onRequestChanges = vi.fn();
    render(<Harness requireNote commentCount={2} onRequestChanges={onRequestChanges} />);

    expect(screen.getByText('2 line comments will go with your note.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Request changes' }));

    expect(onRequestChanges).toHaveBeenCalledOnce();
  });

  it('allows an empty request when the host does not require a note', () => {
    const onRequestChanges = vi.fn();
    render(<Harness onRequestChanges={onRequestChanges} />);

    fireEvent.click(screen.getByRole('button', { name: 'Request changes' }));

    expect(onRequestChanges).toHaveBeenCalledOnce();
  });

  it('holds both answers while busy or disabled, and shows errors', () => {
    const { rerender } = render(<Harness busy />);
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Request changes' })).toBeDisabled();

    rerender(<Harness disabled error="The session refused." />);
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('The session refused.');
  });

  it('names one line comment properly', () => {
    render(<Harness commentCount={1} />);

    expect(screen.getByText('1 line comment will go with your note.')).toBeInTheDocument();
  });
});
