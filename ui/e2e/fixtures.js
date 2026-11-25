// Shared test fixture: every test fails if the page threw an uncaught error.
import { test as base, expect } from '@playwright/test';

export const test = base.extend({
  pageErrors: [async ({ page }, use) => {
    const errors = [];
    page.on('pageerror', (err) => errors.push(err.message));
    await use(errors);
    expect(errors, 'uncaught page errors').toEqual([]);
  }, { auto: true }],
});

export { expect };

// The desktop sidebar; the phone tab bar is also a nav named "Main".
export const sidebar = (page) => page.locator('nav.side');
