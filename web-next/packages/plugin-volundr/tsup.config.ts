import { defineConfig } from 'tsup';
import { execSync } from 'node:child_process';

export default defineConfig({
  entry: ['src/index.ts'],
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
    '@xterm/xterm',
    '@xterm/addon-fit',
    'shiki',
  ],
  onSuccess: async () => {
    execSync('postcss src/styles.css -o dist/styles.css', {
      stdio: 'inherit',
    });
  },
});
