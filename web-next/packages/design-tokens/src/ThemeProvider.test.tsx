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
