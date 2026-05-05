import type { Meta, StoryObj } from '@storybook/react';
import { AuthContext, type AuthContextValue } from './AuthContext';
import { useAuth } from './useAuth';

const meta: Meta = {
  title: 'Auth/AuthProvider',
};
export default meta;

type Story = StoryObj;

function AuthStatusDisplay() {
  const auth = useAuth();
  return (
    <div
      style={{
        padding: 24,
        fontFamily: 'var(--font-mono, monospace)',
        fontSize: 13,
        color: 'var(--color-text-primary, #fafafa)',
        background: 'var(--color-bg-secondary, #18181b)',
        borderRadius: 8,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        maxWidth: 400,
      }}
    >
      <div>
        enabled: <strong>{String(auth.enabled)}</strong>
      </div>
      <div>
        authenticated: <strong>{String(auth.authenticated)}</strong>
      </div>
      <div>
        loading: <strong>{String(auth.loading)}</strong>
      </div>
      <div>
        accessToken:{' '}
        <strong>{auth.accessToken ? `${auth.accessToken.slice(0, 20)}…` : 'null'}</strong>
      </div>
      <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
        <button onClick={auth.login} style={{ padding: '4px 12px', cursor: 'pointer' }}>
          Sign in
        </button>
        <button onClick={auth.logout} style={{ padding: '4px 12px', cursor: 'pointer' }}>
          Sign out
        </button>
      </div>
    </div>
  );
}

const signedInValue: AuthContextValue = {
  enabled: true,
  authenticated: true,
  loading: false,
  user: null,
  accessToken: 'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.stub',
  login: () => alert('login called'),
  logout: () => alert('logout called'),
};

const signedOutValue: AuthContextValue = {
  enabled: true,
  authenticated: false,
  loading: false,
  user: null,
  accessToken: null,
  login: () => alert('login called'),
  logout: () => alert('logout called'),
};

const disabledValue: AuthContextValue = {
  enabled: false,
  authenticated: true,
  loading: false,
  user: null,
  accessToken: null,
  login: () => {},
  logout: () => {},
};

export const SignedIn: Story = {
  render: () => (
    <AuthContext.Provider value={signedInValue}>
      <AuthStatusDisplay />
    </AuthContext.Provider>
  ),
};

export const SignedOut: Story = {
  render: () => (
    <AuthContext.Provider value={signedOutValue}>
      <AuthStatusDisplay />
    </AuthContext.Provider>
  ),
};

export const AuthDisabled: Story = {
  render: () => (
    <AuthContext.Provider value={disabledValue}>
      <AuthStatusDisplay />
    </AuthContext.Provider>
  ),
};
