import { defineConfig } from 'vitest/config';

/** Kept separate from vite.config.ts so the build config carries only build types. */
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
});
