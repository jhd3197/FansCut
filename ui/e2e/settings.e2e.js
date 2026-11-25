import { test, expect } from './fixtures.js';

test('settings renders every section', async ({ page }) => {
  await page.goto('/settings');
  for (const name of ['Sources', 'Output', 'Rendering', 'Cut defaults', 'AI', 'Automation',
    'Filler data', 'Caches & network']) {
    await expect(page.getByRole('heading', { level: 2, name, exact: true })).toBeVisible();
  }
  await expect(page.getByRole('button', { name: 'Save changes' })).toBeVisible();
});

test('caches & network shows the cache rows', async ({ page }) => {
  await page.goto('/settings');
  const section = page.getByRole('region', { name: 'Caches & network' });
  await section.scrollIntoViewIfNeeded();
  for (const row of ['Posters and backdrops', 'Library listings', 'Catalogue lookups']) {
    await expect(section.getByText(row, { exact: true })).toBeVisible();
  }
  await expect(section.getByRole('button', { name: 'Clear' })).toHaveCount(3);
});

test('the logs page loads entries', async ({ page }) => {
  await page.goto('/logs');
  await expect(page.getByRole('heading', { level: 1, name: 'Logs' })).toBeVisible();
  await expect(page.getByText(/\d+ entries/)).toBeVisible();
  await expect(page.getByText(/FansCut .* started/)).toBeVisible();
});
