import { defineConfig } from '@playwright/test';

/**
 * A config for `salaahkaar.spec.ts` ALONE, without the suite's global setup.
 *
 * The suite's `global-setup.ts` teaches a fixture product through `/enrol`,
 * which is a shopkeeper route: with the counter's lock on
 * (`GAWAAH_REQUIRE_AUTH=1`) it answers 401 and the whole run refuses to start.
 * The Salaahkaar spec signs itself in, so it can run against a locked till —
 * which is how the counter is meant to be run — with:
 *
 *     npx playwright test -c e2e/salaahkaar.config.ts
 *
 * Under the default config (lock off, full suite) the same spec runs as one
 * file among the others and its sign-in step finds nothing to do.
 */
export default defineConfig({
  // Relative to THIS file, which lives inside e2e/.
  testDir: '.',
  testMatch: /salaahkaar\.spec\.ts/,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: process.env.GAWAAH_BASE || 'http://127.0.0.1:8790' },
});
