import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { SettingsTopbar } from './SettingsTopbar';

let mockPathname = '/ting/settings';
const navigate = vi.fn();

vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: { select: (s: unknown) => unknown }) =>
    select({ location: { pathname: mockPathname } }),
  useRouter: () => ({
    navigate,
  }),
}));

describe('SettingsTopbar', () => {
  beforeEach(() => {
    navigate.mockReset();
  });

  it('returns null outside Ting settings routes', () => {
    mockPathname = '/ting';
    const { container } = render(<SettingsTopbar />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the fallback label for unknown settings routes and navigates back', () => {
    mockPathname = '/ting/settings/custom';
    render(<SettingsTopbar />);

    expect(screen.getByText('Settings')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Back to Ting'));
    expect(navigate).toHaveBeenCalledWith({ to: '/ting' });
  });
});
