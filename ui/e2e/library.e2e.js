import { test, expect } from './fixtures.js';

test('the library lists the show from the folder source', async ({ page }) => {
  await page.goto('/library');
  await expect(page.getByRole('heading', { name: 'Shows' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Harbor Lights/ })).toBeVisible();
});

test('opening the show page', async ({ page }) => {
  await page.goto('/library');
  await page.getByRole('link', { name: /Harbor Lights/ }).click();
  await expect(page).toHaveURL(/\/series\//);
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor Lights' })).toBeVisible();
  await expect(page).toHaveTitle(/Harbor Lights/);
});
