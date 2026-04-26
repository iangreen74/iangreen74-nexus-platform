// Right-side reports panel — Phase 2 (3 feasible / 9 deferred).
// Lists the catalog from GET /api/reports, lets the operator pick a
// report and run it. Deferred reports render greyed with an enum-
// reason tooltip, surfacing substrate gaps in the UI as a first-class
// signal.
import { useEffect, useState } from 'react';
import { ReportSection } from './ReportSection';
import {
  buildReportCSV, buildReportJSON, downloadFile, reportFilename,
} from '../lib/reportDownload';

type CatalogEntry = {
  report_id: string;
  name: string;
  tier: number;
  description: string;
  params_schema: Record<string, { required?: boolean; description?: string }>;
  feasible_now: boolean;
  deferred_reasons: string[];
};

type RunResult = {
  report_id: string;
  name: string;
  generated_at: string;
  params: Record<string, unknown>;
  sections: Array<{ title: string; kind: string; data: unknown }>;
  deferred_reasons: string[];
};

export function ReportsPanel() {
  const [catalog, setCatalog] = useState<CatalogEntry[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [result, setResult] = useState<RunResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/reports')
      .then((r) => r.json())
      .then((body) => {
        const reports: CatalogEntry[] = body.reports || [];
        // Feasible first, then deferred; tier ascending within each group.
        reports.sort((a, b) => {
          if (a.feasible_now !== b.feasible_now) return a.feasible_now ? -1 : 1;
          return a.tier - b.tier;
        });
        setCatalog(reports);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const selected = catalog?.find((r) => r.report_id === selectedId) ?? null;

  async function handleRun() {
    if (!selected) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await fetch(`/api/reports/${selected.report_id}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });
      if (!r.ok) {
        const text = await r.text().catch(() => '');
        throw new Error(`${r.status}: ${text.slice(0, 200)}`);
      }
      setResult(await r.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="w-[360px] border-l border-op-border bg-op-surface flex flex-col min-h-0">
      <div className="px-3 py-2 border-b border-op-border font-mono text-2xs tracking-wide text-op-accent">
        REPORTS
      </div>

      <div className="px-3 py-2 border-b border-op-border">
        <select
          className="w-full bg-op-bg border border-op-border text-op-text font-mono text-xs px-2 py-1"
          value={selectedId ?? ''}
          onChange={(e) => {
            setSelectedId(e.target.value || null);
            setParams({});
            setResult(null);
            setError(null);
          }}
        >
          <option value="">select a report…</option>
          {catalog?.map((r) => (
            <option
              key={r.report_id}
              value={r.report_id}
              disabled={!r.feasible_now}
              title={
                r.feasible_now
                  ? r.description
                  : `deferred — ${r.deferred_reasons.join(', ')}`
              }
            >
              {r.feasible_now ? '✓ ' : '⏸ '}T{r.tier} · {r.name}
            </option>
          ))}
        </select>
      </div>

      {selected && selected.feasible_now && (
        <div className="px-3 py-2 border-b border-op-border space-y-2">
          {Object.entries(selected.params_schema).map(([k, schema]) => (
            <label key={k} className="block">
              <span className="block font-mono text-2xs text-op-text-dim mb-0.5">
                {k}
                {schema.required ? ' *' : ''}
              </span>
              <input
                className="w-full bg-op-bg border border-op-border text-op-text font-mono text-xs px-2 py-1"
                value={params[k] ?? ''}
                placeholder={schema.description}
                onChange={(e) => setParams({ ...params, [k]: e.target.value })}
              />
            </label>
          ))}
          <button
            onClick={handleRun}
            disabled={loading}
            className="font-mono text-xs border border-op-accent text-op-accent px-3 py-1 hover:bg-op-accent hover:text-op-bg transition-colors disabled:opacity-50"
          >
            {loading ? 'running…' : 'run'}
          </button>
        </div>
      )}

      {selected && !selected.feasible_now && (
        <div className="px-3 py-2 border-b border-op-border font-mono text-2xs text-op-text-dim">
          deferred — substrate gap:
          <ul className="mt-1 list-disc list-inside text-op-warning">
            {selected.deferred_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2">
        {error && (
          <div className="font-mono text-2xs text-op-danger">{error}</div>
        )}
        {result?.deferred_reasons.length ? (
          <div className="font-mono text-2xs text-op-text-dim">
            (deferred — empty envelope)
          </div>
        ) : null}
        {result && (
          <div className="flex gap-1.5 pb-1">
            <button
              type="button"
              disabled={loading || !!error}
              onClick={() => downloadFile(
                reportFilename(result, 'csv'),
                buildReportCSV(result),
                'text/csv;charset=utf-8',
              )}
              className="font-mono text-2xs border border-op-border text-op-text-dim px-2 py-0.5 hover:border-op-accent hover:text-op-accent transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              aria-label="Download report as CSV"
            >
              ↓ csv
            </button>
            <button
              type="button"
              disabled={loading || !!error}
              onClick={() => downloadFile(
                reportFilename(result, 'json'),
                buildReportJSON(result),
                'application/json;charset=utf-8',
              )}
              className="font-mono text-2xs border border-op-border text-op-text-dim px-2 py-0.5 hover:border-op-accent hover:text-op-accent transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              aria-label="Download report as JSON"
            >
              ↓ json
            </button>
          </div>
        )}
        {result?.sections.map((s, i) => (
          <ReportSection key={i} title={s.title} kind={s.kind} data={s.data} />
        ))}
      </div>
    </div>
  );
}
