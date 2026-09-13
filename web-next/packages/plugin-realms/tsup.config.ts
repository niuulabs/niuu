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
    '@niuulabs/domain',
    '@niuulabs/query',
    '@niuulabs/plugin-valkyrie',
    '@niuulabs/plugin-ravn',
    '@niuulabs/plugin-ting',
    '@niuulabs/plugin-volundr',
    '@niuulabs/plugin-mimir',
  ],
  onSuccess: async () => {
    execSync('pnpm exec postcss src/styles.css -o dist/styles.css', { stdio: 'inherit' });
    execSync('cp dist/styles.css dist/index.css', { stdio: 'inherit' });
  },
});
