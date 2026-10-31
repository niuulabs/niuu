import { expect, it } from 'vitest';
import type { Terminal } from '@xterm/xterm';
import { syncTerminalTheme } from './syncTerminalTheme';

it('updates terminal colors without replacing the session and restores its original dark theme', async () => {
  const host = document.createElement('div');
  host.dataset.theme = 'ice';
  host.style.setProperty('--color-bg-primary', '#f7f7f7');
  host.style.setProperty('--color-text-primary', '#171717');
  document.body.appendChild(host);
  const dark = { background: '#09090b', foreground: '#fafafa' };
  const terminal = { options: { theme: dark } } as Terminal;
  const stop = syncTerminalTheme(terminal, host);
  expect(terminal.options.theme).toBe(dark);
  host.dataset.theme = 'light';
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(terminal.options.theme?.background).toBe('#f7f7f7');
  expect(terminal.options.theme?.foreground).toBe('#171717');
  host.dataset.theme = 'ice';
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(terminal.options.theme).toBe(dark);
  stop();
  host.remove();
});
