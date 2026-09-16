import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { ThemeProvider, useTheme } from './ThemeProvider';

function ThemeReader() {
  const { theme } = useTheme();
  return <span data-testid="theme">{theme}</span>;
}

describe('ThemeProvider', () => {
  afterEach(() => {
    cleanup();
    delete document.documentElement.dataset.theme;
  });

  it('defaults to ice and sets data-theme on documentElement', () => {
    render(
      <ThemeProvider>
        <ThemeReader />
      </ThemeProvider>,
    );
    expect(screen.getByTestId('theme').textContent).toBe('ice');
    expect(document.documentElement.dataset.theme).toBe('ice');
  });

  it('respects initial theme prop', () => {
    render(
      <ThemeProvider theme="amber">
        <ThemeReader />
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('amber');
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
