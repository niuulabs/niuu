import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@niuulabs/design-tokens/tokens.css';
import '@niuulabs/ui/styles.css';
import '@niuulabs/shell/index.css';
import '@niuulabs/plugin-login/styles.css';
import '@niuulabs/plugin-mimir/styles.css';
import '@niuulabs/plugin-mimir/index.css';
import '@niuulabs/plugin-observatory/styles.css';
import '@niuulabs/plugin-observatory/index.css';
import '@niuulabs/plugin-ravn/styles.css';
import '@niuulabs/plugin-ravn/index.css';
import '@niuulabs/plugin-tyr/styles.css';
import '@niuulabs/plugin-tyr/index.css';
import '@niuulabs/plugin-volundr/styles.css';
import '@niuulabs/plugin-volundr/index.css';
import './styles.css';
import { setTokenProvider } from '@niuulabs/query';
import { App } from './App';

/**
 * Dev / test hook: if a page script sets `window.__niuuPreAuth.getToken`
 * before this module runs, we register it as the token provider.
 * Used by Playwright e2e tests to inject Bearer tokens without a real OIDC flow.
 * Only active in non-production builds.
 */
if (!import.meta.env.PROD) {
  type PreAuth = { getToken?: () => string | null };
  const preAuth = (window as Window & { __niuuPreAuth?: PreAuth }).__niuuPreAuth;
  if (preAuth?.getToken) {
    setTokenProvider(preAuth.getToken);
  }
}

const el = document.getElementById('root');
if (!el) throw new Error('#root not found');

createRoot(el).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
