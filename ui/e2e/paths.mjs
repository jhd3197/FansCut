// Where the e2e run keeps its throwaway config and files. Fixed (not random)
// so the config, the setup script and the server all agree without passing
// anything around.
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const E2E_PORT = 6598;
export const E2E_ROOT = join(tmpdir(), 'fanscut-e2e');
export const E2E_CONFIG = join(E2E_ROOT, 'config.yaml');
