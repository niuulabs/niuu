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

it('moves the terminal onto the niuu surfaces and leaves its ANSI colours alone', () => {
  const host = document.createElement('div');
  host.dataset.theme = 'niuu';
  host.style.setProperty('--color-bg-primary', '#071019');
  host.style.setProperty('--color-text-primary', '#edf4f7');
  host.style.setProperty('--color-text-secondary', '#a5b7c1');
  document.body.appendChild(host);
  const terminal = {
    options: { theme: { background: '#09090b', foreground: '#fafafa', red: '#ef4444' } },
  } as unknown as Terminal;

  const stop = syncTerminalTheme(terminal, host);

  expect(terminal.options.theme).toMatchObject({
    background: '#071019',
    foreground: '#edf4f7',
    black: '#071019',
    red: '#ef4444',
  });
  stop();
  host.remove();
});
