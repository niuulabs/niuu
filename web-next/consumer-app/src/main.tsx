import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
// CSS must be imported by the consumer — packages ship pre-compiled CSS
import '@niuulabs/design-tokens/tokens.css';
import '@niuulabs/ui/styles.css';
import '@niuulabs/shell/styles.css';
import '@niuulabs/plugin-tyr/styles.css';
import { App } from './App';

const el = document.getElementById('root');
if (!el) throw new Error('#root not found');

createRoot(el).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
