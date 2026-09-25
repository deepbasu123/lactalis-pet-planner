import { describe, it, expect } from 'vitest';
import { filenameFromContentDisposition, normalizeSupply } from './api';

describe('filenameFromContentDisposition', () => {
  it('extracts a quoted filename', () => {
    const header = 'attachment; filename="pet_line_planner_export_20260914_093000.xlsx"';
    expect(filenameFromContentDisposition(header, 'fallback.xlsx')).toBe(
      'pet_line_planner_export_20260914_093000.xlsx',
    );
  });

  it('extracts an unquoted filename', () => {
    const header = 'attachment; filename=report.pdf';
    expect(filenameFromContentDisposition(header, 'fallback.pdf')).toBe('report.pdf');
  });

  it('falls back when the header is null', () => {
    expect(filenameFromContentDisposition(null, 'fallback.xlsx')).toBe('fallback.xlsx');
  });

  it('falls back when the header is undefined', () => {
    expect(filenameFromContentDisposition(undefined, 'fallback.pdf')).toBe('fallback.pdf');
  });

  it('falls back when the header has no filename token', () => {
    expect(filenameFromContentDisposition('attachment', 'fallback.xlsx')).toBe('fallback.xlsx');
  });

  it('handles a filename containing spaces', () => {
    const header = 'attachment; filename="pet line planner export.xlsx"';
    expect(filenameFromContentDisposition(header, 'fallback.xlsx')).toBe(
      'pet line planner export.xlsx',
    );
  });
});

describe('normalizeSupply', () => {
  it('passes a bare array of rows through unchanged', () => {
    const rows = [{ sku_code: 'A', week_key: '2026-W35' }];
    expect(normalizeSupply(rows)).toEqual(rows);
  });

  it('unwraps a { rows: [...] } envelope', () => {
    const rows = [{ sku_code: 'A', week_key: '2026-W35' }];
    expect(normalizeSupply({ rows })).toEqual(rows);
  });

  it('returns [] for null, undefined, or an unexpected shape', () => {
    expect(normalizeSupply(null)).toEqual([]);
    expect(normalizeSupply(undefined)).toEqual([]);
    expect(normalizeSupply({ nope: 1 })).toEqual([]);
    expect(normalizeSupply(42)).toEqual([]);
  });
});
