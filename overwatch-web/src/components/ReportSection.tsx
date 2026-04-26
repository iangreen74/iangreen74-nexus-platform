// One section of a rendered report. Kind drives layout:
//   metric — key/value flat dict
//   table  — { columns, rows }
//   list   — { items }
//   text   — { text }
type Props = {
  title: string;
  kind: string;
  data: unknown;
};

export function ReportSection({ title, kind, data }: Props) {
  return (
    <div className="border border-op-border bg-op-bg p-2">
      <div className="font-mono text-2xs text-op-accent tracking-wide mb-1.5">
        {title}
      </div>
      <Body kind={kind} data={data} />
    </div>
  );
}

function Body({ kind, data }: { kind: string; data: unknown }) {
  if (kind === 'metric') {
    const obj = (data ?? {}) as Record<string, unknown>;
    return (
      <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 font-mono text-2xs">
        {Object.entries(obj).map(([k, v]) => (
          <div key={k} className="contents">
            <span className="text-op-text-dim">{k}</span>
            <span className="text-op-text font-medium truncate">
              {formatScalar(v)}
            </span>
          </div>
        ))}
      </div>
    );
  }

  if (kind === 'table') {
    const t = (data ?? {}) as { columns?: string[]; rows?: Array<Record<string, unknown>> };
    const cols = t.columns ?? [];
    const rows = t.rows ?? [];
    if (rows.length === 0) {
      return <div className="font-mono text-2xs text-op-text-dim">(no rows)</div>;
    }
    return (
      <div className="overflow-x-auto">
        <table className="font-mono text-2xs w-full">
          <thead>
            <tr className="text-op-text-dim border-b border-op-border">
              {cols.map((c) => (
                <th key={c} className="text-left pr-2 pb-1 font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-op-border/50 last:border-0">
                {cols.map((c) => (
                  <td key={c} className="pr-2 py-0.5 text-op-text truncate max-w-[180px]">
                    {formatScalar(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (kind === 'list') {
    const items = ((data ?? {}) as { items?: unknown[] }).items ?? [];
    if (items.length === 0) {
      return <div className="font-mono text-2xs text-op-text-dim">(empty)</div>;
    }
    return (
      <ul className="font-mono text-2xs space-y-0.5 list-disc list-inside">
        {items.map((it, i) => (
          <li key={i} className="text-op-text">
            {typeof it === 'string' ? it : JSON.stringify(it)}
          </li>
        ))}
      </ul>
    );
  }

  if (kind === 'text') {
    const text = ((data ?? {}) as { text?: string }).text ?? '';
    return (
      <div className="font-mono text-2xs text-op-text-dim whitespace-pre-wrap">
        {text}
      </div>
    );
  }

  return (
    <pre className="font-mono text-2xs text-op-text-dim overflow-x-auto">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

function formatScalar(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}
