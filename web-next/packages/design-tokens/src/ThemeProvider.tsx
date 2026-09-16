import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useCallback,
  type ReactNode,
} from 'react';

export type ThemeName = 'ice' | 'amber' | 'spring' | 'xteo';

interface ThemeContextValue {
  theme: ThemeName;
  setTheme: (theme: ThemeName) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

interface ThemeProviderProps {
  theme?: ThemeName;
  children: ReactNode;
}

export function ThemeProvider({ theme: initial = 'ice', children }: ThemeProviderProps) {
  const [theme, updateTheme] = useState<ThemeName>(() => {
    try {
      const saved = localStorage.getItem('niuu.theme');
      if (saved === 'ice' || saved === 'amber' || saved === 'spring' || saved === 'xteo')
        return saved;
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
  }, [theme]);

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within <ThemeProvider>');
  return ctx;
}
