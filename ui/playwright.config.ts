import { defineConfig } from '@playwright/test';

/** End-to-end against the running till. Serial: they share one fake camera. */
export default defineConfig({
  testDir: './e2e',
  // The suite teaches the one product its fake camera names, so a run reports
  // on the CODE and not on whatever happens to be in the shop today.
  globalSetup: './e2e/global-setup.ts',
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: process.env.GAWAAH_BASE || 'http://127.0.0.1:8790' },
});
