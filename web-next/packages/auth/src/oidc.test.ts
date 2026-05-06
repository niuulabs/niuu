import { describe, it, expect, beforeEach } from 'vitest';
import { getOidcConfig, getUserManager, resetUserManager } from './oidc';
import type { NiuuConfig } from '@niuulabs/plugin-sdk';

const baseConfig: NiuuConfig = {
  theme: 'ice',
  plugins: {},
  services: {},
};

describe('getOidcConfig', () => {
  it('returns null when auth is not in config', () => {
    expect(getOidcConfig(baseConfig)).toBeNull();
  });

  it('returns null when issuer is missing', () => {
    const config: NiuuConfig = { ...baseConfig, auth: { clientId: 'niuu-web' } };
    expect(getOidcConfig(config)).toBeNull();
  });

  it('returns null when clientId is missing', () => {
    const config: NiuuConfig = {
      ...baseConfig,
      auth: { issuer: 'https://auth.example.com' },
    };
    expect(getOidcConfig(config)).toBeNull();
  });

  it('returns null when issuer is empty string', () => {
    const config: NiuuConfig = {
      ...baseConfig,
      auth: { issuer: '', clientId: 'niuu-web' },
    };
    expect(getOidcConfig(config)).toBeNull();
  });

  it('returns config when issuer and clientId are set', () => {
    const config: NiuuConfig = {
      ...baseConfig,
      auth: {
        issuer: 'https://auth.example.com',
        clientId: 'niuu-web',
      },
    };

    const oidcConfig = getOidcConfig(config);

    expect(oidcConfig).not.toBeNull();
    expect(oidcConfig!.authority).toBe('https://auth.example.com');
    expect(oidcConfig!.clientId).toBe('niuu-web');
    expect(oidcConfig!.scope).toBe('openid profile email');
    expect(oidcConfig!.redirectUri).toBe(`${window.location.origin}/login/callback`);
    expect(oidcConfig!.postLogoutRedirectUri).toBe(window.location.origin);
  });
});

describe('getUserManager', () => {
  beforeEach(() => {
    resetUserManager();
  });

  it('returns a UserManager instance', () => {
    const config = {
      authority: 'https://auth.example.com',
      clientId: 'niuu-web',
      redirectUri: 'http://localhost:5173',
      postLogoutRedirectUri: 'http://localhost:5173',
      scope: 'openid profile email',
    };

    const mgr = getUserManager(config);

    expect(mgr).toBeDefined();
    expect(mgr.settings.authority).toBe(config.authority);
    expect(mgr.settings.client_id).toBe(config.clientId);
  });

  it('returns the same instance on subsequent calls', () => {
    const config = {
      authority: 'https://auth.example.com',
      clientId: 'niuu-web',
      redirectUri: 'http://localhost:5173',
      postLogoutRedirectUri: 'http://localhost:5173',
      scope: 'openid profile email',
    };

    const mgr1 = getUserManager(config);
    const mgr2 = getUserManager(config);

    expect(mgr1).toBe(mgr2);
  });
});

describe('resetUserManager', () => {
  it('allows a new UserManager to be created after reset', () => {
    const config = {
      authority: 'https://auth.example.com',
      clientId: 'niuu-web',
      redirectUri: 'http://localhost:5173',
      postLogoutRedirectUri: 'http://localhost:5173',
      scope: 'openid profile email',
    };

    const mgr1 = getUserManager(config);
    resetUserManager();
    const mgr2 = getUserManager(config);

    expect(mgr1).not.toBe(mgr2);
  });
});
