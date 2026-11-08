import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import {
  SessionLinksProvider,
  sessionOpenHandler,
  useSessionLinks,
  type SessionLink,
  type SessionLinksValue,
} from './sessionLinks';

const link: SessionLink = {
  id: 's1',
  name: 'coder',
  stateLabel: 'Idle',
  tone: 'muted',
  href: '/sessions/s1',
};

function Probe() {
  const links = useSessionLinks();
  return (
    <span data-testid="probe">
      {links ? (links.resolve('coder')?.name ?? 'none') : 'no provider'}
    </span>
  );
}

describe('sessionLinks', () => {
  it('is null without a provider, so cards render without links', () => {
    render(<Probe />);
    expect(screen.getByTestId('probe')).toHaveTextContent('no provider');
  });

  it('gives the provided resolver to descendants', () => {
    const value: SessionLinksValue = {
      resolve: (key) => (key === 'coder' ? link : null),
      open: vi.fn(),
    };
    render(
      <SessionLinksProvider value={value}>
        <Probe />
      </SessionLinksProvider>,
    );
    expect(screen.getByTestId('probe')).toHaveTextContent('coder');
  });

  it('opens in place on a plain click and leaves modified or non-primary clicks to the browser', () => {
    const open = vi.fn();
    const handler = sessionOpenHandler({ resolve: () => link, open }, link);
    render(
      <a href={link.href} onClick={handler}>
        go
      </a>,
    );
    const anchor = screen.getByRole('link');
    // jsdom cannot navigate; the browser's own handling of the click is not under test.
    anchor.addEventListener('click', (event) => event.preventDefault());
    fireEvent.click(anchor);
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith('s1');
    fireEvent.click(anchor, { metaKey: true });
    fireEvent.click(anchor, { shiftKey: true });
    fireEvent.click(anchor, { button: 1 });
    expect(open).toHaveBeenCalledTimes(1);
  });
});
