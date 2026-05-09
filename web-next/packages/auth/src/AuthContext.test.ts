import { useContext } from 'react';
import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AuthContext } from './AuthContext';

describe('AuthContext', () => {
  it('exposes safe default no-op auth actions', () => {
    const { result } = renderHook(() => useContext(AuthContext));

    expect(result.current.enabled).toBe(false);
    expect(result.current.authenticated).toBe(false);
    expect(result.current.loading).toBe(true);
    expect(result.current.user).toBeNull();
    expect(result.current.accessToken).toBeNull();
    expect(() => result.current.login()).not.toThrow();
    expect(() => result.current.logout()).not.toThrow();
  });
});
