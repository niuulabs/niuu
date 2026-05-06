import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { createElement } from 'react';
import { AuthContext, type AuthContextValue } from './AuthContext';
import { useAccessToken } from './useAccessToken';

function wrapper(value: AuthContextValue) {
  function Wrapper({ children }: { children: React.ReactNode }) {
    return createElement(AuthContext.Provider, { value }, children);
  }
  return Wrapper;
}

describe('useAccessToken', () => {
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

    const { result } = renderHook(() => useAccessToken(), { wrapper: wrapper(value) });

    expect(result.current).toBeNull();
  });

  it('returns the access token when authenticated', () => {
    const value: AuthContextValue = {
      enabled: true,
      authenticated: true,
      loading: false,
      user: null,
      accessToken: 'my-access-token',
      login: vi.fn(),
      logout: vi.fn(),
    };

    const { result } = renderHook(() => useAccessToken(), { wrapper: wrapper(value) });

    expect(result.current).toBe('my-access-token');
  });
});
