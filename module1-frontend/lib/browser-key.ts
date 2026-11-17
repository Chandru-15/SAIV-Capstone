import { ClientDataError } from './api-error';
import type { BrowserChallenge } from './device-api';

const DATABASE = 'saiv-browser-keys';
const STORE = 'signing-keys';
const signingAlgorithm = { name: 'ECDSA', hash: 'SHA-256' };
const keyAlgorithm = { name: 'ECDSA', namedCurve: 'P-256' };
const encoder = new TextEncoder();

export interface BrowserKey {
  privateKey: CryptoKey;
  publicKey: string;
  keyId: string;
}

function requireCrypto(): SubtleCrypto {
  if (!globalThis.isSecureContext || !globalThis.crypto?.subtle || !globalThis.indexedDB) {
    throw new ClientDataError('Browser verification requires HTTPS and a browser that allows site storage.');
  }
  return globalThis.crypto.subtle;
}

function base64url(data: ArrayBuffer): string {
  return btoa(String.fromCharCode(...Array.from(new Uint8Array(data)))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function storageError() {
  return new ClientDataError('Browser key storage is unavailable. Allow site storage and reopen check-in.');
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    let blocked = false;
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onerror = () => reject(storageError());
    request.onblocked = () => { blocked = true; reject(storageError()); };
    request.onsuccess = () => {
      if (blocked) { request.result.close(); return; }
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
  });
}

function isBrowserKey(value: unknown): value is BrowserKey {
  const entry = value as Partial<BrowserKey> | null;
  return !!entry && typeof entry.keyId === 'string' && typeof entry.publicKey === 'string' &&
    typeof CryptoKey !== 'undefined' && entry.privateKey instanceof CryptoKey &&
    entry.privateKey.type === 'private' && !entry.privateKey.extractable &&
    entry.privateKey.algorithm.name === 'ECDSA' &&
    (entry.privateKey.algorithm as EcKeyAlgorithm).namedCurve === 'P-256' &&
    entry.privateKey.usages.includes('sign');
}

async function lookup(userId: string, identifier: string, candidate?: BrowserKey, replaceKeyId?: string): Promise<BrowserKey | null> {
  requireCrypto();
  let db: IDBDatabase;
  try { db = await openDatabase(); }
  catch { throw storageError(); }
  try {
    return await new Promise<BrowserKey | null>((resolve, reject) => {
      const transaction = db.transaction(STORE, candidate ? 'readwrite' : 'readonly');
      const store = transaction.objectStore(STORE);
      const index = [userId, identifier];
      const read = store.get(index);
      let key: BrowserKey | null = null;
      read.onsuccess = () => {
        key = isBrowserKey(read.result) ? read.result : null;
        // Compare under the write transaction so concurrent recovery tabs
        // keep the first replacement rather than overwriting each other.
        if (candidate && (!key || key.keyId === replaceKeyId)) {
          store.put(candidate, index);
          key = candidate;
        }
      };
      transaction.oncomplete = () => resolve(key);
      transaction.onabort = () => reject(storageError());
    });
  } catch { throw storageError(); }
  finally { db.close(); }
}

export async function readBrowserKey(userId: string, identifier: string): Promise<BrowserKey | null> {
  return lookup(userId, identifier);
}

/** Called only after the student explicitly starts enrollment or recovery. */
export async function prepareBrowserKey(
  userId: string, identifier: string, signal: AbortSignal, replaceKeyId?: string,
): Promise<BrowserKey> {
  const subtle = requireCrypto();
  const existing = await readBrowserKey(userId, identifier);
  signal.throwIfAborted();
  if (existing && existing.keyId !== replaceKeyId) return existing;
  const pair = await subtle.generateKey(keyAlgorithm, false, ['sign', 'verify']);
  const spki = await subtle.exportKey('spki', pair.publicKey);
  const digest = await subtle.digest('SHA-256', spki);
  signal.throwIfAborted();
  const key = await lookup(userId, identifier, {
    privateKey: pair.privateKey, publicKey: base64url(spki), keyId: base64url(digest),
  }, replaceKeyId);
  if (!key) throw storageError();
  return key;
}

export async function signBrowserChallenge(key: BrowserKey, challenge: BrowserChallenge, body?: string): Promise<string> {
  const subtle = requireCrypto();
  if (Date.now() >= Date.parse(challenge.expires_at)) {
    throw new ClientDataError('The browser challenge expired. Start a fresh attempt.');
  }
  const message = body === undefined ? challenge.message :
    `${challenge.message}\n${base64url(await subtle.digest('SHA-256', encoder.encode(body)))}`;
  return base64url(await subtle.sign(signingAlgorithm, key.privateKey, encoder.encode(message)));
}
