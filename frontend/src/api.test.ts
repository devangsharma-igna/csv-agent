import { afterEach, describe, expect, it, vi } from 'vitest';
import { askQuery } from './api';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('API error messages', () => {
  it('shows a graceful read-only role message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ detail: { error: 'read_only_role' } }),
        { status: 403, headers: { 'Content-Type': 'application/json' } },
      ),
    ));

    await expect(askQuery('tickets', 'Remove the tickets table')).rejects.toThrow(
      'Your account has read-only access. Only a Super Admin can modify the database.',
    );
  });

  it('uses a structured backend message when available', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            error: 'raw_sql_denied',
            message: 'Raw SQL is not accepted. Describe the operation in natural language.',
          },
        }),
        { status: 400, headers: { 'Content-Type': 'application/json' } },
      ),
    ));

    await expect(askQuery('tickets', 'SELECT * FROM tickets')).rejects.toThrow(
      'Raw SQL is not accepted. Describe the operation in natural language.',
    );
  });
});
