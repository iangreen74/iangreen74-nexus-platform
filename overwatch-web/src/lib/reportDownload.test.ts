import { describe, expect, it } from 'vitest';
import {
  buildReportCSV,
  buildReportJSON,
  reportFilename,
  rowsToCSV,
  sectionToCSV,
  slugify,
  timestampForFilename,
  type ReportRunResult,
  type ReportSection,
} from './reportDownload';

// --- rowsToCSV (RFC4180 escaping) -----------------------------------------

describe('rowsToCSV', () => {
  it('plain rows', () => {
    expect(rowsToCSV(['a', 'b'], [{ a: 1, b: 'hello' }])).toBe('a,b\n1,hello');
  });

  it('quotes a cell containing a comma', () => {
    expect(rowsToCSV(['a'], [{ a: 'has,comma' }])).toBe('a\n"has,comma"');
  });

  it('escapes internal double-quotes by doubling them', () => {
    expect(rowsToCSV(['a'], [{ a: 'has"quote' }])).toBe('a\n"has""quote"');
  });

  it('quotes a cell containing a newline', () => {
    expect(rowsToCSV(['a'], [{ a: 'has\nnewline' }])).toBe('a\n"has\nnewline"');
  });

  it('null cell becomes empty', () => {
    expect(rowsToCSV(['a'], [{ a: null }])).toBe('a\n');
  });

  it('undefined cell becomes empty', () => {
    expect(rowsToCSV(['a'], [{}])).toBe('a\n');
  });

  it('object cell is JSON-encoded then escaped', () => {
    expect(rowsToCSV(['a'], [{ a: { x: 1 } }])).toBe('a\n"{""x"":1}"');
  });

  it('empty rows yields just the header line', () => {
    expect(rowsToCSV(['a', 'b'], [])).toBe('a,b');
  });
});

// --- sectionToCSV (each kind) --------------------------------------------

describe('sectionToCSV', () => {
  it('metric becomes a 2-column key/value table', () => {
    const s: ReportSection = {
      title: 'Fleet totals', kind: 'metric',
      data: { total: 3, green: 1, amber: 1, red: 1 },
    };
    expect(sectionToCSV(s)).toBe('key,value\ntotal,3\ngreen,1\namber,1\nred,1');
  });

  it('table emits header + rows', () => {
    const s: ReportSection = {
      title: 'Per-tenant', kind: 'table',
      data: {
        columns: ['tenant_id', 'status'],
        rows: [{ tenant_id: 'forge-x', status: 'green' }],
      },
    };
    expect(sectionToCSV(s)).toBe('tenant_id,status\nforge-x,green');
  });

  it('list one column "item"; each item compact-JSON if non-string', () => {
    const s: ReportSection = {
      title: 'Top troubled', kind: 'list',
      data: {
        items: ['plain string', { tenant_id: 'forge-x', reason: 'sad' }],
      },
    };
    expect(sectionToCSV(s)).toBe(
      'item\nplain string\n"{""tenant_id"":""forge-x"",""reason"":""sad""}"',
    );
  });

  it('text becomes a "note" cell with the text body', () => {
    const s: ReportSection = {
      title: '7-day trend', kind: 'text',
      data: { text: 'deferred — needs snapshot history' },
    };
    expect(sectionToCSV(s)).toBe('note\ndeferred — needs snapshot history');
  });

  it('text with embedded newlines is properly quoted', () => {
    const s: ReportSection = {
      title: 'Notes', kind: 'text',
      data: { text: 'line1\nline2' },
    };
    expect(sectionToCSV(s)).toBe('note\n"line1\nline2"');
  });

  it('unknown kind yields a "raw" JSON dump cell', () => {
    const s: ReportSection = {
      title: 'Mystery', kind: 'gizmo',
      data: { foo: 'bar' },
    };
    expect(sectionToCSV(s)).toBe('raw\n"{""foo"":""bar""}"');
  });

  it('empty list yields just the header', () => {
    const s: ReportSection = {
      title: 'Empty', kind: 'list', data: { items: [] },
    };
    expect(sectionToCSV(s)).toBe('item');
  });
});

// --- buildReportCSV (multi-section + comment block) -----------------------

describe('buildReportCSV', () => {
  const sample: ReportRunResult = {
    report_id: 'fleet_health',
    name: 'Fleet Health Overview',
    generated_at: '2026-04-26T15:00:00+00:00',
    params: {},
    deferred_reasons: [],
    sections: [
      { title: 'Fleet totals', kind: 'metric',
        data: { total: 2, green: 2 } },
      { title: 'Per-tenant', kind: 'table',
        data: { columns: ['tenant_id', 'status'],
                rows: [{ tenant_id: 'forge-x', status: 'green' }] } },
      { title: 'Top troubled', kind: 'list',
        data: { items: [] } },
      { title: '7-day trend', kind: 'text',
        data: { text: 'deferred' } },
    ],
  };

  it('starts with the comment block', () => {
    const csv = buildReportCSV(sample);
    expect(csv.startsWith('# Report: Fleet Health Overview\n')).toBe(true);
    expect(csv).toContain('# Generated: 2026-04-26T15:00:00+00:00');
    expect(csv).toContain('# Params: {}');
  });

  it('emits one labeled block per section', () => {
    const csv = buildReportCSV(sample);
    expect(csv).toContain('# Section: Fleet totals (metric)');
    expect(csv).toContain('# Section: Per-tenant (table)');
    expect(csv).toContain('# Section: Top troubled (list)');
    expect(csv).toContain('# Section: 7-day trend (text)');
  });

  it('multi-table report renders every table block', () => {
    const r: ReportRunResult = {
      report_id: 'tenant_profile',
      name: 'Tenant Operational Profile',
      generated_at: '2026-04-26T15:00:00+00:00',
      params: { tenant_id: 'forge-x' },
      deferred_reasons: [],
      sections: [
        { title: 'ECS services', kind: 'table',
          data: { columns: ['name'], rows: [{ name: 'svc-a' }] } },
        { title: 'ALB target health', kind: 'table',
          data: { columns: ['tg'], rows: [{ tg: 'tg-a' }] } },
      ],
    };
    const csv = buildReportCSV(r);
    expect(csv).toContain('# Section: ECS services (table)\nname\nsvc-a');
    expect(csv).toContain('# Section: ALB target health (table)\ntg\ntg-a');
  });

  it('records deferred-reasons in the header when present', () => {
    const r: ReportRunResult = {
      ...sample,
      deferred_reasons: ['requires_phase_0b_log_correlation'],
      sections: [],
    };
    expect(buildReportCSV(r)).toContain(
      '# Deferred-reasons: requires_phase_0b_log_correlation',
    );
  });

  it('empty sections list still emits just the comment block', () => {
    const r: ReportRunResult = { ...sample, sections: [] };
    const csv = buildReportCSV(r);
    expect(csv).toBe(
      '# Report: Fleet Health Overview\n' +
      '# Generated: 2026-04-26T15:00:00+00:00\n' +
      '# Params: {}',
    );
  });

  it('embeds non-empty params as JSON in the header', () => {
    const r: ReportRunResult = {
      ...sample, params: { tenant_id: 'forge-1dba4143ca24ed1f' },
    };
    expect(buildReportCSV(r)).toContain(
      '# Params: {"tenant_id":"forge-1dba4143ca24ed1f"}',
    );
  });
});

// --- JSON, slug, filename, timestamp -------------------------------------

describe('buildReportJSON', () => {
  it('round-trips the envelope with 2-space indent', () => {
    const r: ReportRunResult = {
      report_id: 'x', name: 'X', generated_at: 'now',
      params: {}, deferred_reasons: [], sections: [],
    };
    const json = buildReportJSON(r);
    expect(json).toContain('  "report_id"');
    expect(JSON.parse(json)).toEqual(r);
  });
});

describe('slugify', () => {
  it('converts mixed-case and special chars to kebab-case', () => {
    expect(slugify('Fleet Health Overview!')).toBe('fleet-health-overview');
  });

  it('strips leading and trailing dashes', () => {
    expect(slugify('  Fleet  ')).toBe('fleet');
  });

  it('collapses multiple separators', () => {
    expect(slugify('a___b---c')).toBe('a-b-c');
  });
});

describe('timestampForFilename', () => {
  it('formats YYYY-MM-DD-HH-MM with zero-padded fields', () => {
    // 2026-04-09T03:07 in local time
    const d = new Date(2026, 3, 9, 3, 7);
    expect(timestampForFilename(d)).toBe('2026-04-09-03-07');
  });
});

describe('reportFilename', () => {
  it('builds <slug>-<ts>.<ext>', () => {
    const r: ReportRunResult = {
      report_id: 'fleet_health', name: 'Fleet Health',
      generated_at: '', params: {}, deferred_reasons: [], sections: [],
    };
    const d = new Date(2026, 3, 26, 15, 30);
    expect(reportFilename(r, 'csv', d)).toBe('fleet-health-2026-04-26-15-30.csv');
    expect(reportFilename(r, 'json', d)).toBe('fleet-health-2026-04-26-15-30.json');
  });
});
