// Browser end-to-end tests against the real server.
//
//   npm run build      # the server serves ui/dist, so build first
//   npm run e2e        # = playwright test
//
// The webServer command writes a throwaway config, data dir and fixture media
// folder into <tmp>/fanscut-e2e (e2e/global-setup.mjs), then starts
// `python -m fanscut serve` on port 6598 from the repo root.
import { defineConfig, devices } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { E2E_CONFIG, E2E_PORT } from './e2e/paths.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const python = process.env.PYTHON || 'python';

export default defineConfig({
  testDir: './e2e',
  // *.e2e.js, not *.spec.js: Vitest's default glob would pick those up
  testMatch: '**/*.e2e.js',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: `http://127.0.0.1:${E2E_PORT}`,
    trace: 'on-first-retry',
    video: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `node e2e/global-setup.mjs && ${python} -m fanscut --config "${E2E_CONFIG}" serve`,
    cwd: here,
    url: `http://127.0.0.1:${E2E_PORT}/healthz`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: 'ignore',
    stderr: 'pipe',
    env: {
      FANSCUT_AUTODETECT: '0',
      FANSCUT_CONFIG: E2E_CONFIG,
      // run from ui/: the package resolves from the repo root
      PYTHONPATH: resolve(here, '..'),
    },
  },
});
