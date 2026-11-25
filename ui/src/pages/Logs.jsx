// The server log, newest first, filtered by level and text, a page at a time.
import { useSearchParams } from 'react-router';
import { useDebounced, usePageTitle } from '../lib/hooks.js';
import { useApi } from '../lib/store.js';
import { ErrorBox, Loading } from '../components/ui.jsx';
import { t } from '../i18n/index.js';

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'];
const TAKE = 100;

export function Logs() {
  usePageTitle(t('logs.title'));
  const [params, setParams] = useSearchParams();
  const level = params.get('level') || 'INFO';
  const search = params.get('q') || '';
  const page = Math.max(0, Number(params.get('page') || 0));
  const q = useDebounced(search, 300);
  const set = (k, v) => setParams((p) => {
    const n = new URLSearchParams(p);
    if (v) n.set(k, v); else n.delete(k);
    if (k !== 'page') n.delete('page');
    return n;
  }, { replace: true });
  const path = `/logs?level=${level}&search=${encodeURIComponent(q)}&skip=${page * TAKE}&take=${TAKE}`;
  const { data, error, loading, reload } = useApi(path);
  const info = data?.page_info;
  return (
    <div className="page page-wide">
      <div className="page-head">
        <div>
          <h1>{t('logs.title')}</h1>
          <p className="muted">{t('logs.hint')}</p>
        </div>
        <button className="btn" type="button" onClick={reload}>{t('common.refresh')}</button>
      </div>
      <div className="toolbar">
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <select className="inp" aria-label="Lowest level" value={level} onChange={(e) => set('level', e.target.value)}>
            {LEVELS.map((l) => <option key={l} value={l}>{l} and up</option>)}
          </select>
          <input className="inp" type="search" placeholder="Search the log" aria-label="Search the log"
            value={search} onChange={(e) => set('q', e.target.value)} />
        </div>
        {info && <span className="muted tiny">{t('logs.entries', { n: info.results })}</span>}
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {loading && <Loading />}
      {data && (
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table className="table logs">
            <tbody>
              {data.results.map((e, i) => (
                <tr key={`${page}-${i}`} className={`log-${e.level.toLowerCase()}`}>
                  <td className="mono tiny" style={{ whiteSpace: 'nowrap' }}>{e.ts}</td>
                  <td className="mono tiny">{e.level}</td>
                  <td className="mono tiny muted">{e.logger}</td>
                  <td className="mono tiny" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {e.message}{e.exc ? `\n${e.exc}` : ''}
                  </td>
                </tr>
              ))}
              {!data.results.length && <tr><td className="muted pad">{t('logs.empty')}</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {info && info.pages > 1 && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button className="btn btn-sm" type="button" disabled={page === 0} onClick={() => set('page', String(page - 1))}>Newer</button>
          <span className="muted tiny">Page {page + 1} of {info.pages}</span>
          <button className="btn btn-sm" type="button" disabled={page + 1 >= info.pages} onClick={() => set('page', String(page + 1))}>Older</button>
        </div>
      )}
    </div>
  );
}
