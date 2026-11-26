// Where the e2e run keeps its throwaway config and files. Fixed (not random)
// so the config, the setup script and the server all agree without passing
// anything around.
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const E2E_PORT = 6598;
// inside the checkout (gitignored), not the shared system temp folder
export const E2E_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '.e2e-run');
export const E2E_CONFIG = join(E2E_ROOT, 'config.yaml');
