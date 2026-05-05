import { defineConfig } from 'tsup';
import { execSync } from 'node:child_process';

export default defineConfig({
  entry: ['src/index.tsx'],
  format: ['esm'],
  dts: true,
  sourcemap: true,
  clean: true,
  external: [
    'react',
    '@tanstack/react-query',
    '@tanstack/react-router',
    '@niuulabs/plugin-sdk',
    '@niuulabs/ui',
  ],
  onSuccess: async () => {
    execSync('postcss src/styles.css -o dist/styles.css', { stdio: 'inherit' });
  },
});
