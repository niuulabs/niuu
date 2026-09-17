import '@testing-library/jest-dom/vitest';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { useAuth } from '@niuulabs/auth';
import { TooltipProvider } from '@niuulabs/ui';
import { AccountControls } from './AccountControls';
vi.mock('@niuulabs/auth', () => ({ useAuth: vi.fn() }));
const login = vi.fn();
const onDisconnect = vi.fn();
function auth(overrides: Record<string, unknown> = {}) {
  vi.mocked(useAuth).mockReturnValue({
    enabled: true,
    authenticated: true,
    loading: false,
    user: { profile: { name: 'Reviewer' } },
    login,
    ...overrides,
  } as unknown as ReturnType<typeof useAuth>);
}
describe('account controls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth();
  });
  it('shows the user and disconnects through the host callback', () => {
    render(
      <TooltipProvider>
        <AccountControls onDisconnect={onDisconnect} />
      </TooltipProvider>,
    );
    expect(screen.getByText('Reviewer')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(onDisconnect).toHaveBeenCalledOnce();
    expect(login).not.toHaveBeenCalled();
  });
  it('offers sign in for a signed-out account', () => {
    auth({ authenticated: false, user: null });
    render(
      <TooltipProvider>
        <AccountControls onDisconnect={onDisconnect} />
      </TooltipProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(login).toHaveBeenCalledOnce();
  });
  it('uses a local disconnect for a private connection without OIDC', () => {
    auth({ enabled: false, authenticated: false, user: null });
    render(
      <TooltipProvider>
        <AccountControls onDisconnect={onDisconnect} />
      </TooltipProvider>,
    );
    expect(screen.queryByText('Private connection')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Disconnect' }).textContent).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(onDisconnect).toHaveBeenCalledOnce();
  });
});
