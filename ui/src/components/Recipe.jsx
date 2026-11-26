import { useRef, useState } from 'react';
import { api, seriesPath } from '../lib/api.js';
import { useAct } from '../lib/hooks.js';
import { Dialog, Notice } from './ui.jsx';

export function downloadJson(name, data) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}

export const recipeFileName = (title) => `${title.replace(/[^\w.-]+/g, '_') || 'series'}.fanscut.json`;

// Paste community cut notes (episode lists, timestamps, rationale); the configured
// LLM turns them into a recipe. Preview shows what was understood before applying.
function AIRecipeForm({ base, onDone, onImported }) {
  const { act, busy } = useAct();
  const [notes, setNotes] = useState('');
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const run = (apply) => act(async () => {
    setError(null);
    try {
      const r = await api(`${base}/recipe/ai`, { method: 'POST', body: { notes, apply } });
      if (apply) {
        onImported();
        onDone(`Cut applied to ${r.import.applied} episode(s)`);
        return r;
      }
      setPreview(r.recipe);
      return r;
    } catch (e) {
      setError(e.message);
      throw e;
    }
  });
  const n = preview ? Object.keys(preview.episodes || {}).length : 0;
  return (
    <div className="stack">
      <p className="muted" style={{ margin: 0 }}>
        Paste what a fan editor published about their cut: which episodes they dropped or kept,
        timestamps, reasons. The LLM set in Settings turns it into a cut. Nothing is
        applied until you press Apply.
      </p>
      <textarea className="input" rows={10} style={{ width: '100%', fontFamily: 'inherit' }}
        placeholder={'e.g. "Dropped S01E02 (recap episode). In S01E04 I only kept the dock fight, 10:20 to 14:05…"'}
        value={notes} onChange={(e) => setNotes(e.target.value)} aria-label="Fan-edit notes" />
      {error && <div className="notice notice-err" role="alert">{error}</div>}
      {preview && (
        <div className="notice notice-ok">
          <span>Cut ready: {n} episode(s) touched, preset “{preview.preset || 'unchanged'}”.
            Review the episodes after applying.</span>
        </div>
      )}
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button type="button" className="btn" onClick={() => run(false)} disabled={busy || !notes.trim()}>Preview</button>
        {preview && <button type="button" className="btn btn-primary" onClick={() => run(true)} disabled={busy}>Apply</button>}
      </div>
    </div>
  );
}

// Export downloads the recipe JSON; import applies an uploaded one by episode number.
export function RecipeButtons({ series, onImported, onAi }) {
  const { act, msg, clear } = useAct();
  const file = useRef(null);
  const [aiOpen, setAiOpen] = useState(false);
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
    }, (r) => `Cut applied to ${r.applied} episode(s)${r.skipped.length ? `; ${r.skipped.length} not in this library (${r.skipped.slice(0, 5).join(', ')})` : ''}`);
  };
  return (
    <>
      <button type="button" className="btn" onClick={() => act(async () => downloadJson(recipeFileName(series.title), await api(`${base}/recipe`)))}>Export cut</button>
      <button type="button" className="btn" onClick={() => file.current.click()}>Import cut</button>
      <button type="button" className="btn" onClick={() => (onAi ? onAi() : setAiOpen(true))} title="Turn community cut notes into a cut with the configured LLM">AI cut…</button>
      <input ref={file} type="file" accept="application/json,.json" hidden onChange={onFile} data-testid="recipe-file" />
      {aiOpen && (
        <Dialog title="AI cut from notes" onClose={() => setAiOpen(false)} wide>
          <AIRecipeForm base={base} onImported={onImported} onDone={() => setAiOpen(false)} />
        </Dialog>
      )}
      {msg && <div className="inline-notice"><Notice msg={msg} onClose={clear} /></div>}
    </>
  );
}

// The "Recipe" action of the show and season pages: export, import or AI recipe,
// in one dialog. A recipe holds labels, overrides, trims and segments, never media.
export function RecipeDialog({ series, onClose, onImported }) {
  const [ai, setAi] = useState(false);
  const [done, setDone] = useState(null);
  const base = seriesPath(series.key);
  return (
    <Dialog title={ai ? 'AI cut from notes' : `Cut file · ${series.title}`} onClose={onClose} wide>
      {ai ? (
        <AIRecipeForm base={base} onImported={onImported}
          onDone={(text) => { setDone({ ok: true, text }); setAi(false); }} />
      ) : (
        <div className="stack">
          <p className="muted" style={{ margin: 0 }}>
            A cut file is a small JSON file with this show’s labels, keep choices, trims and custom
            segments. It holds no video, so you can share it. Importing applies it by episode number.
          </p>
          <Notice msg={done} onClose={() => setDone(null)} />
          <div className="row wrap"><RecipeButtons series={series} onImported={onImported} onAi={() => setAi(true)} /></div>
        </div>
      )}
    </Dialog>
  );
}
