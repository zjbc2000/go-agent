// Idempotency key and ID generation utilities.

let counter = 0;

export function generateRequestId(): string {
  counter += 1;
  return `req_${Date.now()}_${counter}`;
}

export function generateId(prefix: string): string {
  counter += 1;
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}
