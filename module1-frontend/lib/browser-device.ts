import { ClientDataError } from './api-error';

// Preserve the identifier used before registration was introduced.
export const BROWSER_DEVICE_KEY = 'saiv_device_fingerprint_v1';
const uuidV4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Read only. Viewing settings must never mint or persist a browser identifier. */
export function readBrowserDeviceIdentifier(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const existing = window.localStorage.getItem(BROWSER_DEVICE_KEY);
    return existing && uuidV4.test(existing) ? existing : null;
  } catch { throw storageError(); }
}

/** Application-scoped browser installation ID, not hardware attestation. */
export function getBrowserDeviceIdentifier(): string {
  if (typeof window === 'undefined') throw new ClientDataError('Browser registration is available only in the browser.');
  let existing: string | null;
  try { existing = window.localStorage.getItem(BROWSER_DEVICE_KEY); }
  catch { throw storageError(); }
  if (existing && uuidV4.test(existing)) return existing;

  const crypto = window.crypto;
  let identifier: string;
  if (crypto?.randomUUID) identifier = crypto.randomUUID();
  else if (crypto?.getRandomValues) {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
    identifier = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  } else throw new ClientDataError('Secure browser identification is unavailable. Use a supported browser over HTTPS.');
  if (!uuidV4.test(identifier)) throw new ClientDataError('Secure browser identification failed. Try a supported browser.');

  try {
    window.localStorage.setItem(BROWSER_DEVICE_KEY, identifier);
    if (window.localStorage.getItem(BROWSER_DEVICE_KEY) !== identifier) throw storageError();
  } catch { throw storageError(); }
  return identifier;
}

function storageError(): ClientDataError {
  return new ClientDataError('Browser storage is unavailable. Allow site storage and reopen check-in. Registration cannot safely use a new temporary identifier on every visit.');
}
