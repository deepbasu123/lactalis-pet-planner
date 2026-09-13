import { describe, it, expect } from 'vitest';
import { filenameFromContentDisposition } from './api';

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
