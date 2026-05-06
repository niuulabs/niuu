import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThemeProvider } from './ThemeContext';
import { useTheme } from './useTheme';
import { THEMES } from './themes';

const store: Record<string, string> = {};

const localStorageMock = {
  getItem: vi.fn((k: string) => store[k] ?? null),
  setItem: vi.fn((k: string, v: string) => {
    store[k] = v;
  }),
  removeItem: vi.fn((k: string) => {
    delete store[k];
  }),
  clear: vi.fn(() => {
    for (const k of Object.keys(store)) delete store[k];
  }),
  key: vi.fn(),
  length: 0,
};

function TestConsumer() {
  const { theme, setTheme } = useTheme();
  return (
    <div>
      <span data-testid="current">{theme}</span>
      {THEMES.map(t => (
        <button key={t.id} onClick={() => setTheme(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

describe('ThemeContext', () => {
  beforeEach(() => {
    for (const k of Object.keys(store)) delete store[k];
    vi.restoreAllMocks();
    Object.defineProperty(window, 'localStorage', { value: localStorageMock, writable: true });
    document.documentElement.removeAttribute('data-theme');
  });

  it('defaults to "default" theme', () => {
    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );
    expect(screen.getByTestId('current').textContent).toBe('default');
    expect(document.documentElement.getAttribute('data-theme')).toBeNull();
  });

  it('switches to spring theme and sets data-theme attribute', () => {
    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    fireEvent.click(screen.getByText('Spring Green'));
    expect(screen.getByTestId('current').textContent).toBe('spring');
    expect(document.documentElement.getAttribute('data-theme')).toBe('spring');
  });

  it('persists theme to localStorage', () => {
    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    fireEvent.click(screen.getByText('Spring Green'));
    expect(localStorageMock.setItem).toHaveBeenCalledWith('volundr-theme', 'spring');
  });

  it('restores theme from localStorage', () => {
    store['volundr-theme'] = 'spring';

    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    expect(screen.getByTestId('current').textContent).toBe('spring');
    expect(document.documentElement.getAttribute('data-theme')).toBe('spring');
  });

  it('falls back to default for invalid stored value', () => {
    store['volundr-theme'] = 'nonexistent';

    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    expect(screen.getByTestId('current').textContent).toBe('default');
  });

  it('removes data-theme attribute when switching back to default', () => {
    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>
    );

    fireEvent.click(screen.getByText('Spring Green'));
    expect(document.documentElement.getAttribute('data-theme')).toBe('spring');

    fireEvent.click(screen.getByText('Volundr Amber'));
    expect(document.documentElement.getAttribute('data-theme')).toBeNull();
  });

  it('exports all expected themes', () => {
    expect(THEMES).toHaveLength(3);
    expect(THEMES.map(t => t.id)).toEqual(['default', 'spring', 'frost']);
  });
});
