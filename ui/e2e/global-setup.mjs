// Builds the throwaway world the e2e server runs in: a temp folder with a
// config, a data and output dir, and a tiny media library for the folder
// source. Run by playwright.config.js just before `python -m fanscut serve`
// (as part of the webServer command, so it always precedes the server).
//
// Usage: node e2e/global-setup.mjs   (prints nothing; exits non-zero on error)
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { E2E_PORT, E2E_ROOT, E2E_CONFIG } from './paths.mjs';

const SHOW = 'Harbor Lights';
const EPISODES = [
  ['Season 01', 'Harbor Lights - S01E01.mkv'],
  ['Season 01', 'Harbor Lights - S01E02.mkv'],
  ['Season 01', 'Harbor Lights - S01E03.mkv'],
  ['Season 02', 'Harbor Lights - S02E01.mkv'],
];

rmSync(E2E_ROOT, { recursive: true, force: true });
const media = join(E2E_ROOT, 'media');
for (const [season, file] of EPISODES) {
  const dir = join(media, SHOW, season);
  mkdirSync(dir, { recursive: true });
  // placeholder bytes: the folder source lists by filename, nothing probes them here
  writeFileSync(join(dir, file), 'placeholder');
}
mkdirSync(join(E2E_ROOT, 'data'), { recursive: true });
mkdirSync(join(E2E_ROOT, 'output'), { recursive: true });

// JSON is valid YAML, so the config needs no YAML writer.
const config = {
  server: { host: '127.0.0.1', port: E2E_PORT, auth: 'none' },
  paths: { data_dir: join(E2E_ROOT, 'data'), output_dir: join(E2E_ROOT, 'output') },
  library: { folder: { path: media } },
  // no internet during the run: catalogues from cache only, no filler fetches
  catalogues: { offline: true },
  fillers: { use_jikan: false },
  // nothing scheduled in the background while the specs click around
  schedule: {
    prune: '', sweep_renders: '', library_refresh: '', fillers_refresh: '',
    cut_updates: '', library_check: '',
  },
};
writeFileSync(E2E_CONFIG, `${JSON.stringify(config, null, 2)}\n`);
