import type { Terminal } from '@xterm/xterm';

/** Update colors in place so changing the theme keeps terminal sessions alive. */
export function syncTerminalTheme(terminal: Terminal, element: HTMLElement): () => void {
  const darkTheme = terminal.options.theme;
  const update = () => {
    if (element.closest('[data-theme]')?.getAttribute('data-theme') !== 'light') {
      terminal.options.theme = darkTheme;
      return;
    }
    const styles = getComputedStyle(element);
    const color = (token: string) => styles.getPropertyValue(token).trim();
    terminal.options.theme = {
      ...darkTheme,
      background: color('--color-bg-primary'),
      foreground: color('--color-text-primary'),
      cursor: color('--color-brand'),
      cursorAccent: color('--color-bg-primary'),
      selectionBackground: color('--color-bg-elevated'),
      selectionForeground: color('--color-text-primary'),
      black: color('--color-text-primary'),
      white: color('--color-text-secondary'),
      brightWhite: color('--color-text-primary'),
      red: color('--color-critical'),
      brightRed: color('--color-critical'),
      green: color('--status-healthy'),
      brightGreen: color('--status-healthy'),
      yellow: color('--color-accent-amber'),
      brightYellow: color('--color-accent-amber'),
      blue: color('--status-running'),
      brightBlue: color('--status-running'),
      magenta: color('--color-accent-violet'),
      brightMagenta: color('--color-accent-violet'),
      cyan: color('--color-accent-teal'),
      brightCyan: color('--color-accent-teal'),
    };
  };
  update();
  const observer = new MutationObserver(update);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
    subtree: true,
  });
  return () => observer.disconnect();
}
