import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { UI_MODE_STORAGE_KEY } from '@niuulabs/shell';
import { VolundrSessionsRoute } from './routes';

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useParams: () => ({}),
}));

vi.mock('./SessionsPage', () => ({
  SessionsPage: () => <div data-testid="advanced-sessions" />,
}));

vi.mock('./SimpleSessionsPage', () => ({
  SimpleSessionsPage: () => <div data-testid="simple-sessions" />,
}));

describe('VolundrSessionsRoute', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders the calm list in Simple mode', () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
    render(<VolundrSessionsRoute />);
    expect(screen.getByTestId('simple-sessions')).toBeInTheDocument();
  });

  it('renders the full forge console in Advanced mode', () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'advanced');
    render(<VolundrSessionsRoute />);
    expect(screen.getByTestId('advanced-sessions')).toBeInTheDocument();
  });
});
