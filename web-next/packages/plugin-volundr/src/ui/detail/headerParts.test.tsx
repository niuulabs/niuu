import { fireEvent, render, screen } from '@testing-library/react';
import { Archive, Trash2 } from 'lucide-react';
import { describe, expect, it, vi } from 'vitest';
import { ContextMeter } from './ContextMeter';
import { SessionMoreMenu } from './SessionMoreMenu';
import { Subline } from './Subline';

describe('Subline', () => {
  it('joins the pieces that exist with a dot and leaves the rest out', () => {
    render(
      <Subline
        data-testid="line"
        items={[
          { id: 'a', node: <span>first</span> },
          null,
          false,
          undefined,
          '',
          { id: 'b', node: <span>second</span> },
          { id: 'c', node: <span>third</span> },
        ]}
      />,
    );

    expect(screen.getByTestId('line')).toHaveTextContent('first·second·third');
  });
});

describe('ContextMeter', () => {
  it('renders nothing without context or cost', () => {
    const { container } = render(<ContextMeter context={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('marks a nearly full window as a warning', () => {
    render(<ContextMeter context={{ percent: 95, used: 190, window: 200 }} />);
    expect(screen.getByTestId('session-meter').querySelector('.forge-meter__fill--warn')).not.toBe(
      null,
    );
  });

  it('keeps the normal colour at the warning threshold', () => {
    render(<ContextMeter context={{ percent: 80, used: 160, window: 200 }} cost="$0.41" />);
    const meter = screen.getByTestId('session-meter');
    expect(meter.querySelector('.forge-meter__fill--warn')).toBeNull();
    expect(meter).toHaveTextContent('80%·$0.41');
  });
});

describe('SessionMoreMenu', () => {
  it('renders nothing when there are no actions', () => {
    const { container } = render(<SessionMoreMenu items={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('runs an action once and closes', () => {
    const onArchive = vi.fn();
    render(
      <SessionMoreMenu
        items={[
          { id: 'archive', label: 'Archive session', icon: Archive, onSelect: onArchive },
          {
            id: 'delete',
            label: 'Delete session',
            icon: Trash2,
            onSelect: vi.fn(),
            tone: 'critical',
            separated: true,
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(screen.getByRole('button', { name: 'Archive session' }));

    expect(onArchive).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Archive session' })).not.toBeInTheDocument();
  });

  it('does not run a disabled action', () => {
    const onSelect = vi.fn();
    render(
      <SessionMoreMenu
        items={[{ id: 'a', label: 'Resend', icon: Archive, onSelect, disabled: true }]}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(screen.getByRole('button', { name: 'Resend' }));

    expect(onSelect).not.toHaveBeenCalled();
  });
});
