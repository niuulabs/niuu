import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HowItAnsweredPanel } from './HowItAnsweredPanel';

function setup(overrides: Partial<React.ComponentProps<typeof HowItAnsweredPanel>> = {}) {
  const props: React.ComponentProps<typeof HowItAnsweredPanel> = {
    question: 'how do we deploy?',
    mountName: undefined,
    resultCount: 2,
    isLoading: false,
    isError: false,
    elapsedSeconds: 0.42,
    onFollowUp: vi.fn(),
    ...overrides,
  };
  render(<HowItAnsweredPanel {...props} />);
  return props;
}

describe('HowItAnsweredPanel', () => {
  it('shows the question, mount searched, and result count', () => {
    setup();
    expect(screen.getByText('"how do we deploy?"')).toBeInTheDocument();
    expect(screen.getByText('every mount')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('shows the searched mount when scoped', () => {
    setup({ mountName: 'platform' });
    expect(screen.getByText('platform')).toBeInTheDocument();
  });

  it('shows elapsed time when known', () => {
    setup({ elapsedSeconds: 1.234 });
    expect(screen.getByText('1.23s')).toBeInTheDocument();
  });

  it('shows a loading state while searching', () => {
    setup({ isLoading: true });
    expect(screen.getByText('searching memory…')).toBeInTheDocument();
  });

  it('shows an error state', () => {
    setup({ isError: true });
    expect(screen.getByRole('alert')).toHaveTextContent('Search failed.');
  });

  it('calls onFollowUp from the Follow up button', async () => {
    const props = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Follow up' }));
    expect(props.onFollowUp).toHaveBeenCalled();
  });
});
