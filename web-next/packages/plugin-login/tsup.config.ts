import { defineConfig } from 'tsup';
import { writeFileSync } from 'node:fs';
import { concatCssFiles } from '../../build/concat-css';

export default defineConfig({
  entry: ['src/index.ts'],
  format: ['esm'],
  dts: true,
  sourcemap: true,
  clean: true,
  external: ['react', '@tanstack/react-router', '@niuulabs/auth', '@niuulabs/plugin-sdk'],
  onSuccess: async () => {
    writeFileSync('dist/styles.css', concatCssFiles('src'));
  },
});
