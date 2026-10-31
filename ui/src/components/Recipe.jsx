import { useRef } from 'react';
import { api, seriesPath } from '../lib/api.js';
import { useAct } from '../lib/hooks.js';
import { Notice } from './ui.jsx';

export function downloadJson(name, data) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}

export const recipeFileName = (title) => `${title.replace(/[^\w.-]+/g, '_') || 'series'}.fanscut.json`;

// Export downloads the recipe JSON; import applies an uploaded one by episode number.
export function RecipeButtons({ series, onImported }) {
  const { act, msg, clear } = useAct();
  const file = useRef(null);
  const base = seriesPath(series.key);
  const onFile = async (e) => {
    const f = e.target.files[0];
    e.target.value = '';
    if (!f) return;
    await act(async () => {
      let doc;
      try { doc = JSON.parse(await f.text()); } catch { throw new Error('that file is not valid JSON'); }
      const r = await api(`${base}/recipe`, { method: 'POST', body: doc });
      onImported();
      return r;
    }, (r) => `Recipe applied to ${r.applied} episode(s)${r.skipped.length ? `; ${r.skipped.length} not in this library (${r.skipped.slice(0, 5).join(', ')})` : ''}`);
  };
  return (
    <>
      <button className="btn" onClick={() => act(async () => downloadJson(recipeFileName(series.title), await api(`${base}/recipe`)))}>Export recipe</button>
      <button className="btn" onClick={() => file.current.click()}>Import recipe</button>
      <input ref={file} type="file" accept="application/json,.json" hidden onChange={onFile} data-testid="recipe-file" />
      {msg && <div className="inline-notice"><Notice msg={msg} onClose={clear} /></div>}
    </>
  );
}
