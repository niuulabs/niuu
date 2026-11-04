import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useCallback,
  type ReactNode,
} from 'react';

const THEME_NAMES = [
  'niuu',
  'ice',
  'amber',
  'spring',
  'xteo',
  'midnight-blue',
  'deep-blue',
  'dark-blue',
  'light',
] as const;
export type ThemeName = (typeof THEME_NAMES)[number];

function isThemeName(value: string | null): value is ThemeName {
  return THEME_NAMES.some((name) => name === value);
}

interface ThemeContextValue {
  theme: ThemeName;
  setTheme: (theme: ThemeName) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

interface ThemeProviderProps {
  theme?: ThemeName;
  children: ReactNode;
}

export function ThemeProvider({ theme: initial = 'niuu', children }: ThemeProviderProps) {
  const [theme, updateTheme] = useState<ThemeName>(() => {
    try {
      const saved = localStorage.getItem('niuu.theme');
      if (isThemeName(saved)) return saved;
    } catch {
      /* Storage can be disabled; the configured theme still works. */
    }
    return initial;
  });
  const setTheme = useCallback((next: ThemeName) => {
    updateTheme(next);
    try {
      localStorage.setItem('niuu.theme', next);
    } catch {
      /* Theme changes remain available for this visit without storage. */
    }
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    const browserColor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    const surface = getComputedStyle(document.documentElement)
      .getPropertyValue('--color-bg-primary')
      .trim();
    if (browserColor && surface) browserColor.content = surface;
  }, [theme]);

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within <ThemeProvider>');
  return ctx;
}
