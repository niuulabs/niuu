import type { Meta, StoryObj } from '@storybook/react';
import { AuthContext, type AuthContextValue } from '@niuulabs/auth';
import { CallbackPage } from './CallbackPage';

const meta: Meta<typeof CallbackPage> = {
  title: 'Login/CallbackPage',
  component: CallbackPage,
  parameters: {
    layout: 'fullscreen',
  },
};

export default meta;
type Story = StoryObj<typeof CallbackPage>;

const processingAuth: AuthContextValue = {
  enabled: true,
  authenticated: false,
  loading: true,
  user: null,
  accessToken: null,
  login: () => {},
  logout: () => {},
};

/** Processing state — AuthProvider is exchanging the OIDC code for a token. */
export const Processing: Story = {
  render: () => (
    <AuthContext.Provider value={processingAuth}>
      <CallbackPage />
    </AuthContext.Provider>
  ),
};
