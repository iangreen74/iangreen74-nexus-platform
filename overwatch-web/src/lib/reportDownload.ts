// Report download helpers — Smart CSV + JSON.
//
// Operator clicks `Download CSV` after a report run; we emit a
// multi-block CSV that preserves every section in the envelope:
//
//   # Report: <name>
//   # Generated: <iso>
//   # Params: <json>
//
//   # Section: <title> (<kind>)
//   <header>
//   <rows>
//
//   # Section: <title> (<kind>)
//   ...
//
// metric → 2-column key/value
// table  → full columns + rows
// list   → single column where each row is a compact JSON encoding of
//          the item (handles nested objects without flattening)
// text   → single column "note" with the text body — "deferred" notes
//          matter for triage and shouldn't be silently dropped.
//
// JSON download is the lossless fallback for whatever the CSV can't
// represent natively.

export type ReportSection = {
  title: string;
  kind: string;
  data: unknown;
};

export type ReportRunResult = {
  report_id: string;
  name: string;
  generated_at: string;
  params: Record<string, unknown>;
  sections: ReportSection[];
  deferred_reasons: string[];
};

/** RFC4180-ish escape: wrap in quotes if the cell contains comma,
 * quote, or newline; double internal quotes. */
function escapeCell(cell: unknown): string {
  if (cell === null || cell === undefined) return '';
  const s = typeof cell === 'string' ? cell : JSON.stringify(cell);
  if (s.includes(',') || s.includes('"') || s.includes('\n') || s.includes('\r')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

export function rowsToCSV(columns: string[], rows: Array<Record<string, unknown>>): string {
  const headerLine = columns.map(escapeCell).join(',');
  const rowLines = rows.map((row) =>
    columns.map((c) => escapeCell(row[c])).join(',')
  );
  return [headerLine, ...rowLines].join('\n');
}

/** Render one section as its CSV block (no leading "# Section" comment;
 * `buildReportCSV` adds that). Returns "" for sections with no data
 * to render (e.g. an empty list). */
export function sectionToCSV(section: ReportSection): string {
  const data = section.data ?? {};

  if (section.kind === 'metric') {
    const obj = data as Record<string, unknown>;
    const lines = ['key,value'];
    for (const [k, v] of Object.entries(obj)) {
      lines.push(`${escapeCell(k)},${escapeCell(v)}`);
    }
    return lines.join('\n');
  }

  if (section.kind === 'table') {
    const t = data as { columns?: string[]; rows?: Array<Record<string, unknown>> };
    const cols = t.columns ?? [];
    const rows = t.rows ?? [];
    return rowsToCSV(cols, rows);
  }

  if (section.kind === 'list') {
    const items = ((data as { items?: unknown[] }).items) ?? [];
    const lines = ['item'];
    for (const it of items) {
      const cell =
        typeof it === 'string' ? it : JSON.stringify(it);
      lines.push(escapeCell(cell));
    }
    return lines.join('\n');
  }

  if (section.kind === 'text') {
    const text = ((data as { text?: string }).text) ?? '';
    return ['note', escapeCell(text)].join('\n');
  }

  // Unknown kind — JSON-dump the whole data object as one cell so it's
  // not lost. The "kind" comment line above will tell the reader what
  // they're looking at.
  return ['raw', escapeCell(JSON.stringify(data))].join('\n');
}

export function buildReportCSV(result: ReportRunResult): string {
  const lines: string[] = [];
  // Leading comment block — preserve metadata for triage context.
  lines.push(`# Report: ${result.name}`);
  lines.push(`# Generated: ${result.generated_at}`);
  lines.push(`# Params: ${JSON.stringify(result.params || {})}`);
  if (result.deferred_reasons?.length) {
    lines.push(`# Deferred-reasons: ${result.deferred_reasons.join(', ')}`);
  }

  for (const section of result.sections || []) {
    lines.push('');
    lines.push(`# Section: ${section.title} (${section.kind})`);
    lines.push(sectionToCSV(section));
  }
  return lines.join('\n');
}

export function buildReportJSON(result: ReportRunResult): string {
  return JSON.stringify(result, null, 2);
}

/** Slugify a report id or name to a filename-safe stem. */
export function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
}

/** Local-time YYYY-MM-DD-HH-MM for filename embedding. */
export function timestampForFilename(d: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return [
    d.getFullYear(),
    pad(d.getMonth() + 1),
    pad(d.getDate()),
    pad(d.getHours()),
    pad(d.getMinutes()),
  ].join('-');
}

export function reportFilename(result: ReportRunResult, ext: string,
                                d: Date = new Date()): string {
  return `${slugify(result.report_id)}-${timestampForFilename(d)}.${ext}`;
}

/** Trigger a browser download via Blob + invisible anchor click. */
export function downloadFile(
  filename: string, content: string, mimeType: string,
): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
