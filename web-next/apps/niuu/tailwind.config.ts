import type { Config } from 'tailwindcss';
import preset from '@niuulabs/design-tokens/tailwind.preset';

export default {
  presets: [preset],
  content: ['src/**/*.{ts,tsx,css}', 'index.html'],
} satisfies Config;
