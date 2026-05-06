export type ThemeId = 'default' | 'spring' | 'frost';

export interface ThemeOption {
  id: ThemeId;
  label: string;
}

export const THEMES: ThemeOption[] = [
  { id: 'default', label: 'Volundr Amber' },
  { id: 'spring', label: 'Spring Green' },
  { id: 'frost', label: 'Frost' },
];

export const STORAGE_KEY = 'volundr-theme';
