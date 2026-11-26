import { test, expect, sidebar } from './fixtures.js';

test('the shell loads with the sidebar', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveTitle('Discover · FansCut');
  const side = sidebar(page);
  await expect(side).toBeVisible();
  await expect(side.getByRole('link', { name: 'FansCut home' })).toBeVisible();
  await expect(page.locator('nav.tabbar')).toBeHidden();
});

test('the sidebar reaches every section', async ({ page }) => {
  await page.goto('/');
  const side = sidebar(page);
  const sections = [
    ['Library', '/library', /Library · FansCut/],
    ['My cuts', '/my-cuts', /My cuts · FansCut/],
    ['Jobs', '/jobs', /Jobs · FansCut/],
    ['Health', '/health', /Health · FansCut/],
    ['Settings', '/settings', /Settings · FansCut/],
    ['Discover', '/', /Discover · FansCut/],
  ];
  for (const [name, path, title] of sections) {
    // a nav count may follow the name ("Discover 2")
    const link = side.locator('.nav').getByRole('link', { name: new RegExp(`^${name}(\\s|$)`) });
    await link.click();
    await expect(page).toHaveURL(path);
    await expect(page).toHaveTitle(title);
    await expect(link).toHaveClass(/active/);
  }
  // Logs lives in the sidebar footer
  await side.getByRole('link', { name: 'Logs' }).click();
  await expect(page).toHaveURL('/logs');
  await expect(page.getByRole('heading', { level: 1, name: 'Logs' })).toBeVisible();
});

test('an unknown route shows the empty state', async ({ page }) => {
  await page.goto('/no-such-page');
  await expect(page.getByText('Nothing here.')).toBeVisible();
  await page.getByRole('link', { name: 'Back to Discover' }).click();
  await expect(page).toHaveURL('/');
});

test.describe('phone layout', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('shows the tab bar and hides the sidebar', async ({ page }) => {
    await page.goto('/');
    const tabbar = page.locator('nav.tabbar');
    await expect(tabbar).toBeVisible();
    await expect(sidebar(page)).toBeHidden();
    await tabbar.getByRole('link', { name: 'Library' }).click();
    await expect(page).toHaveURL('/library');
    await expect(page.getByRole('link', { name: /Harbor Lights/ })).toBeVisible();
  });
});
