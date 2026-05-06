import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { createElement } from 'react';
import { AuthContext, type AuthContextValue } from './AuthContext';
import { useUser } from './useUser';
import type { User } from 'oidc-client-ts';

function wrapper(value: AuthContextValue) {
  function Wrapper({ children }: { children: React.ReactNode }) {
    return createElement(AuthContext.Provider, { value }, children);
  }
  return Wrapper;
}

describe('useUser', () => {
  it('returns null when not authenticated', () => {
    const value: AuthContextValue = {
      enabled: true,
      authenticated: false,
      loading: false,
      user: null,
      accessToken: null,
      login: vi.fn(),
      logout: vi.fn(),
    };

    const { result } = renderHook(() => useUser(), { wrapper: wrapper(value) });

    expect(result.current).toBeNull();
  });

  it('returns the user when authenticated', () => {
    const mockUser = { access_token: 'tok', expired: false } as unknown as User;
    const value: AuthContextValue = {
      enabled: true,
      authenticated: true,
      loading: false,
      user: mockUser,
      accessToken: 'tok',
      login: vi.fn(),
      logout: vi.fn(),
    };

    const { result } = renderHook(() => useUser(), { wrapper: wrapper(value) });

    expect(result.current).toBe(mockUser);
  });
});
