import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { ThemeProvider, useTheme, type ThemeName } from './ThemeProvider';

function ThemeReader() {
  const { theme } = useTheme();
  return <span data-testid="theme">{theme}</span>;
}

describe('ThemeProvider', () => {
  afterEach(() => {
    cleanup();
    localStorage.removeItem('niuu.theme');
    document.head.querySelector('meta[name="theme-color"]')?.remove();
    delete document.documentElement.dataset.theme;
  });

  it('defaults to niuu and sets data-theme on documentElement', () => {
    render(
      <ThemeProvider>
        <ThemeReader />
      </ThemeProvider>,
    );
    expect(screen.getByTestId('theme').textContent).toBe('niuu');
    expect(document.documentElement.dataset.theme).toBe('niuu');
  });

  it('respects initial theme prop', () => {
    render(
      <ThemeProvider theme="amber">
        <ThemeReader />
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('amber');
  });

  it.each<ThemeName>(['xteo', 'midnight-blue', 'deep-blue', 'dark-blue', 'light'])(
    'switches to %s and restores it after remounting',
    (next) => {
      function ChooseTheme() {
        const { setTheme } = useTheme();
        return <button onClick={() => setTheme(next)}>Choose</button>;
      }
      const view = render(
        <ThemeProvider>
          <ChooseTheme />
        </ThemeProvider>,
      );
      fireEvent.click(screen.getByRole('button', { name: 'Choose' }));
      expect(document.documentElement.dataset.theme).toBe(next);
      expect(localStorage.getItem('niuu.theme')).toBe(next);
      view.unmount();
      render(
        <ThemeProvider>
          <ThemeReader />
        </ThemeProvider>,
      );
      expect(screen.getByTestId('theme')).toHaveTextContent(next);
    },
  );

  it('ignores a stored theme that no longer exists', () => {
    localStorage.setItem('niuu.theme', 'ultraviolet');
    render(
      <ThemeProvider theme="deep-blue">
        <ThemeReader />
      </ThemeProvider>,
    );
    expect(screen.getByTestId('theme')).toHaveTextContent('deep-blue');
  });

  it('paints the browser chrome with the theme surface', () => {
    const meta = document.createElement('meta');
    meta.name = 'theme-color';
    meta.content = '#000000';
    document.head.append(meta);
    document.documentElement.style.setProperty('--color-bg-primary', '#101216');
    render(
      <ThemeProvider>
        <ThemeReader />
      </ThemeProvider>,
    );
    document.documentElement.style.removeProperty('--color-bg-primary');
    expect(meta.content).toBe('#101216');
  });

  it('throws when useTheme is used outside the provider', () => {
    const originalError = console.error;
    console.error = () => {};
    expect(() => render(<ThemeReader />)).toThrow(/ThemeProvider/);
    console.error = originalError;
  });
});

function ThemeSwitch() {
  const { theme, setTheme } = useTheme();
  return <button onClick={() => setTheme(theme === 'xteo' ? 'ice' : 'xteo')}>{theme}</button>;
}
it('remembers the selected theme and restores the original dark palette after reload', async () => {
  const { fireEvent } = await import('@testing-library/react');
  localStorage.removeItem('niuu.theme');
  const first = render(
    <ThemeProvider theme="xteo">
      <ThemeSwitch />
    </ThemeProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'xteo' }));
  expect(document.documentElement.dataset.theme).toBe('ice');
  expect(localStorage.getItem('niuu.theme')).toBe('ice');
  first.unmount();
  const second = render(
    <ThemeProvider theme="xteo">
      <ThemeReader />
    </ThemeProvider>,
  );
  expect(screen.getByTestId('theme')).toHaveTextContent('ice');
  second.unmount();
  localStorage.removeItem('niuu.theme');
});

it('restores a saved light theme', () => {
  localStorage.setItem('niuu.theme', 'light');
  const view = render(
    <ThemeProvider theme="xteo">
      <ThemeReader />
    </ThemeProvider>,
  );
  expect(document.documentElement.dataset.theme).toBe('light');
  view.unmount();
  localStorage.removeItem('niuu.theme');
});
