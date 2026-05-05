import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

export type ThemeName = 'ice' | 'amber' | 'spring';

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
  const [theme, setTheme] = useState<ThemeName>(initial);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const value = useMemo(() => ({ theme, setTheme }), [theme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within <ThemeProvider>');
  return ctx;
}
