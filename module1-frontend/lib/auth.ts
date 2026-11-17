import type { User } from '../types/api';
import { ClientDataError } from './api-error';

const ACCESS_TOKEN_KEY = 'saiv_access_token';
const REFRESH_TOKEN_KEY = 'saiv_refresh_token';
const USER_KEY = 'saiv_user';

// Identity changes invalidate pending work. Token rotation keeps this identity.
let sessionVersion = 0;
// Failed removal must not make leftover credentials usable again in this page.
let sessionInvalidated = false;
let cleanupUnconfirmed = false;
const listeners = new Set<() => void>();

export function getAuthCleanupWarning(): string | null {
  return cleanupUnconfirmed
    ? "We couldn't confirm that your sign-in details were cleared. Clear this site's browser data before sharing this device. Reloading may restore your previous sign-in."
    : null;
}

export function getSessionVersion(): number { return sessionVersion; }
export function subscribeAuthSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
function identityChanged(): void {
  sessionVersion++;
  listeners.forEach((listener) => listener());
}

export function isCurrentSession(version: number): boolean {
  return version === sessionVersion && !!getAccessToken();
}

export function rotateTokens(version: number, accessToken: string, refreshToken: string): boolean {
  if (!isCurrentSession(version)) return false;
  writeTokens(accessToken, refreshToken);
  return true;
}

function readSessionItem(key: string): string | null {
  if (typeof window === 'undefined' || sessionInvalidated) return null;
  try {
    return sessionStorage.getItem(key);
  } catch {
    clearAuthSession();
    return null;
  }
}

export function getAccessToken(): string | null {
  return credentialValue(readSessionItem(ACCESS_TOKEN_KEY));
}

export function getRefreshToken(): string | null {
  return credentialValue(readSessionItem(REFRESH_TOKEN_KEY));
}

// Empty fallback values and malformed storage values are never credentials.
// Token authenticity and expiry still belong to the backend, not this check.
function credentialValue(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 &&
    !/[\s\x00-\x1f\x7f]/.test(value) && !/^(null|undefined)$/i.test(value) ? value : null;
}

function storageFailure(): never {
  clearAuthSession();
  throw new ClientDataError('Browser session storage is unavailable. Allow site storage and sign in again.');
}

function writeTokens(accessToken: string, refreshToken: string): void {
  if (!credentialValue(accessToken) || !credentialValue(refreshToken)) {
    clearAuthSession();
    throw new ClientDataError('Invalid authentication response. Please sign in again.');
  }
  try {
    sessionStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
    sessionStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
    if (sessionStorage.getItem(ACCESS_TOKEN_KEY) !== accessToken ||
        sessionStorage.getItem(REFRESH_TOKEN_KEY) !== refreshToken) throw new Error('Storage write was not retained.');
  } catch {
    storageFailure();
  }
}

export function saveTokens(
  accessToken: string,
  refreshToken: string
): void {
  if (typeof window === 'undefined') {
    return;
  }

  writeTokens(accessToken, refreshToken);
  sessionInvalidated = false;
  cleanupUnconfirmed = false;
  identityChanged();
}

export function saveUser(user: User): void {
  if (typeof window === 'undefined') {
    return;
  }

  // A profile must never be saved into an identity invalidated by logout.
  if (sessionInvalidated) return;
  try {
    const storedUser = JSON.stringify(user);
    sessionStorage.setItem(USER_KEY, storedUser);
    if (sessionStorage.getItem(USER_KEY) !== storedUser) throw new Error('Storage write was not retained.');
  } catch {
    storageFailure();
  }
}

export function getStoredUser(): User | null {
  const storedUser = readSessionItem(USER_KEY);

  if (!storedUser) {
    return null;
  }

  try {
    return JSON.parse(storedUser) as User;
  } catch {
    return null;
  }
}

export function clearAuthSession(): void {
  if (typeof window === 'undefined') {
    return;
  }

  sessionInvalidated = true;
  sessionVersion++;
  let cleared = true;
  for (const key of [ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, USER_KEY]) {
    // Do not short-circuit: attempt every key even if another is inaccessible.
    if (!clearStoredItem(key)) cleared = false;
  }
  cleanupUnconfirmed = !cleared;
  // Notify after the synchronous verification so the login page sees its result.
  listeners.forEach((listener) => listener());
}

function clearStoredItem(key: string): boolean {
  try {
    sessionStorage.removeItem(key);
    const value = sessionStorage.getItem(key);
    if (value === null || value === '') return true;
  } catch { /* Removal or verification failed: try overwriting this entry. */ }
  try { sessionStorage.setItem(key, ''); } catch { /* A read may still confirm removal. */ }
  try {
    const value = sessionStorage.getItem(key);
    return value === null || value === '';
  } catch {
    return false;
  }
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}
