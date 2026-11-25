// "New cut from template": pick a template, the films or show, preview the repo files, create.
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { api } from '../lib/api.js';
import { useAct, useLoad } from '../lib/hooks.js';
import { Dialog, ErrorBox, Loading, Notice } from './ui.jsx';

export function TemplateDialog({ onClose, movies: preMovies = [], seriesKey = '', template = '' }) {
  const nav = useNavigate();
  const tq = useLoad(() => api('/templates'));
  const mq = useLoad(() => api('/library/movies'));
  const sq = useLoad(() => api('/library/series'));
  const [tid, setTid] = useState(template || (seriesKey ? 'chronological' : 'trilogy-merge'));
  const [title, setTitle] = useState('');
  const [picked, setPicked] = useState(preMovies);
  const [series, setSeries] = useState(seriesKey);
  const [push, setPush] = useState(false);
  const [repoName, setRepoName] = useState('');
  const [files, setFiles] = useState(null);
  const { act, msg, busy, clear } = useAct();

  if (tq.error) return <Dialog title="New cut from template" onClose={onClose}><ErrorBox error={tq.error} /></Dialog>;
  if (!tq.data) return <Dialog title="New cut from template" onClose={onClose}><Loading /></Dialog>;
  const t = tq.data.templates.find((x) => x.id === tid);
  const body = {
    title, movies: picked, series_key: series,
    push: push && repoName ? { name: repoName, private: true } : null,
  };
  const toggle = (k) => setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]));
  const onDisk = (mq.data?.movies || []).filter((m) => m.on_disk);

  return (
    <Dialog title="New cut from template" onClose={onClose} wide>
      <Notice msg={msg} onClose={clear} />
      <fieldset>
        <legend>Template</legend>
        <div className="form-grid">
          {tq.data.templates.map((x) => (
            <label key={x.id} className="choice">
              <input type="radio" name="tpl" checked={tid === x.id} onChange={() => { setTid(x.id); setFiles(null); }} />
              <span className="stack" style={{ gap: 2 }}><b>{x.label}</b><span className="muted tiny">{x.description}</span></span>
            </label>
          ))}
        </div>
      </fieldset>
      <label className="field">Title
        <input className="input" value={title} placeholder="Leave empty for a default" onChange={(e) => setTitle(e.target.value)} />
      </label>
      {t?.needs === 'movies' ? (
        <fieldset>
          <legend>Films, in order ({picked.length} picked)</legend>
          {picked.length > 0 && (
            <ol className="tiny" style={{ margin: 0, paddingLeft: 20 }}>
              {picked.map((k) => <li key={k}>{onDisk.find((m) => m.key === k)?.title || k}</li>)}
            </ol>
          )}
          <div style={{ maxHeight: 220, overflowY: 'auto', border: '1px solid var(--line)', borderRadius: 6, padding: 8 }}>
            {onDisk.map((m) => (
              <label key={m.key} className="check tiny" style={{ padding: '3px 0' }}>
                <input type="checkbox" checked={picked.includes(m.key)} onChange={() => toggle(m.key)} />
                {m.title} {m.year && <span className="muted">{m.year}</span>}
                {m.edition && <span className="muted">· {m.edition}</span>}
              </label>
            ))}
          </div>
        </fieldset>
      ) : (
        <label className="field">Show
          <select className="input" value={series} onChange={(e) => setSeries(e.target.value)}>
            <option value="">Pick a show…</option>
            {(sq.data?.series || []).map((s) => <option key={s.key} value={s.key}>{s.title}</option>)}
          </select>
        </label>
      )}
      <label className="check">
        <input type="checkbox" checked={push} onChange={(e) => setPush(e.target.checked)} />
        Also push to a new private GitHub repo
      </label>
      {push && (
        <label className="field">Repo name
          <input className="input" value={repoName} placeholder="took" onChange={(e) => setRepoName(e.target.value)} />
          <span className="dim">Needs a GitHub token in Settings.</span>
        </label>
      )}
      {files && (
        <div className="stack" style={{ gap: 6 }}>
          {Object.entries(files).map(([name, text]) => (
            <details key={name}><summary className="mono tiny">{name}</summary><pre className="pre">{text}</pre></details>
          ))}
        </div>
      )}
      <div className="row wrap">
        <button className="btn" disabled={busy} onClick={() => act(
          () => api(`/templates/${tid}/preview`, { method: 'POST', body }),
        ).then((r) => r && setFiles(r.files))}>Preview files</button>
        <button className="btn btn-primary" disabled={busy || (t?.needs === 'movies' ? picked.length === 0 : !series)}
          onClick={() => act(() => api(`/templates/${tid}/create`, { method: 'POST', body }))
            .then((r) => r && nav(`/cuts/${encodeURIComponent(r.id)}`))}>Create cut</button>
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
      </div>
      <p className="dim tiny" style={{ margin: 0 }}>
        Creates README.md, fanscut.json and outline.md in your data folder and opens the new cut.
        The recipe holds timestamps only, never video.
      </p>
    </Dialog>
  );
}
