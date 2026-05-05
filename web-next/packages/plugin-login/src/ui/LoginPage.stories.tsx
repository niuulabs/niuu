import type { Meta, StoryObj } from '@storybook/react';
import { AuthContext, type AuthContextValue } from '@niuulabs/auth';
import { LoginPage } from './LoginPage';

const meta: Meta<typeof LoginPage> = {
  title: 'Login/LoginPage',
  component: LoginPage,
  parameters: {
    layout: 'fullscreen',
  },
};

export default meta;
type Story = StoryObj<typeof LoginPage>;

const signedOutAuth: AuthContextValue = {
  enabled: true,
  authenticated: false,
  loading: false,
  user: null,
  accessToken: null,
  login: () => alert('OIDC redirect would happen here'),
  logout: () => {},
};

const loadingAuth: AuthContextValue = {
  ...signedOutAuth,
  loading: true,
};

/** Default signed-out state — shows the login card with Continue with passkey button. */
export const SignedOut: Story = {
  render: () => (
    <AuthContext.Provider value={signedOutAuth}>
      <LoginPage />
    </AuthContext.Provider>
  ),
};

/** Loading / redirecting state — button shows spinner and is disabled. */
export const Loading: Story = {
  render: () => (
    <AuthContext.Provider value={loadingAuth}>
      <LoginPage />
    </AuthContext.Provider>
  ),
};

/** OIDC failure — error banner appears above the button. */
export const OidcError: Story = {
  render: () => (
    <AuthContext.Provider value={signedOutAuth}>
      <LoginPage
        oidcError="access_denied"
        oidcErrorDescription="The user denied the authorization request."
      />
    </AuthContext.Provider>
  ),
};

/** Constellation ambient background. */
export const AmbientConstellation: Story = {
  render: () => (
    <AuthContext.Provider value={signedOutAuth}>
      <LoginPage ambient="constellation" />
    </AuthContext.Provider>
  ),
};

/** Lattice ambient background. */
export const AmbientLattice: Story = {
  render: () => (
    <AuthContext.Provider value={signedOutAuth}>
      <LoginPage ambient="lattice" />
    </AuthContext.Provider>
  ),
};

/** Topology ambient background (default). */
export const AmbientTopology: Story = {
  render: () => (
    <AuthContext.Provider value={signedOutAuth}>
      <LoginPage ambient="topology" />
    </AuthContext.Provider>
  ),
};
