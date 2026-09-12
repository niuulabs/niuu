import { defineConfig } from 'tsup';
import { writeFileSync } from 'node:fs';
import { concatCssFiles } from '../../build/concat-css';

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
    '@niuulabs/ui',
  ],
  onSuccess: async () => {
    writeFileSync('dist/styles.css', concatCssFiles('src'));
  },
});
