import { defineConfig } from 'tsup';
import { execSync } from 'node:child_process';

export default defineConfig({
  entry: ['src/index.ts', 'src/plugin.tsx'],
  format: ['esm'],
  dts: true,
  sourcemap: true,
  clean: true,
  external: [
    'react',
    '@tanstack/react-query',
    '@tanstack/react-router',
    '@niuulabs/plugin-sdk',
    '@niuulabs/query',
    '@niuulabs/ui',
  ],
  onSuccess: async () => {
    execSync('postcss src/styles.css -o dist/styles.css', {
      stdio: 'inherit',
    });
  },
});
