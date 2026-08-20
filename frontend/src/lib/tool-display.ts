/** Converts arbitrary SSE tool payloads into safe text for React rendering. */
export function formatToolPayload(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value == null) return '';

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
