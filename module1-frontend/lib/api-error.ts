import axios from 'axios';

export type ApiErrorKind = 'http' | 'network' | 'timeout' | 'cancelled' | 'unexpected';
export interface ApiError {
  kind: ApiErrorKind;
  status?: number;
  code?: string;
  message: string;
  retryAfterSeconds?: number;
}

/** Only application-authored errors may display Error.message without an HTTP envelope. */
export class ClientDataError extends Error {}
export class SignedRequestRefreshError extends ClientDataError {}

const messages: Record<number, string> = {
  400: 'The request was not accepted. Check your details and try again.',
  401: 'Your session has expired. Please sign in again.',
  403: 'You do not have permission to perform this action.',
  404: 'This resource is unavailable. Refresh and try again.',
  409: 'This operation conflicts with an existing record. Refresh to check its status.',
  422: 'Please check the supplied details and try again.',
  429: 'Too many requests. Please wait before trying again.',
  500: 'The server could not complete the request. Please try again later.',
  503: 'A required service is unavailable. Please try again later.',
};

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function safeMessage(value: unknown, sensitive: string[]): string | undefined {
  if (typeof value !== 'string') return;
  const text = value.trim();
  if (!text || text.length > 400 || /[\r\n\x00-\x08]|<\/?[a-z][^>]*>/i.test(text) ||
      /traceback|stack trace|data:image\/|(?:[A-Z]:\\|\/home\/|\/app\/)|(?:password|secret|token|authorization|base64)\s*[:=]|bearer\s+\S+|eyJ[A-Za-z0-9_-]+\.|[A-Za-z0-9+/=_-]{64,}/i.test(text) ||
      sensitive.some((entry) => entry.length >= 3 && text.includes(entry))) return;
  return text;
}

export function parseApiError(error: unknown): ApiError {
  if (axios.isCancel(error) || (error instanceof DOMException && error.name === 'AbortError')) {
    return { kind: 'cancelled', message: '' };
  }
  if (error instanceof ClientDataError) {
    return { kind: 'unexpected', message: error.message };
  }
  if (!axios.isAxiosError(error)) {
    return { kind: 'unexpected', message: 'Unable to complete the request. Please try again.' };
  }
  if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
    return { kind: 'timeout', message: 'The request timed out. Check your connection and try again.' };
  }
  if (!error.response) return { kind: 'network', message: 'Unable to reach the server. Check your connection and try again.' };
  const status = error.response.status;
  const fallback = messages[status] ?? 'Unable to complete the request. Please try again.';
  // Never reflect request values, input/ctx validation fields, arbitrary objects or stack traces.
  const sensitive: string[] = [];
  const collect = (value: unknown, key = ''): void => {
    if (typeof value === 'string' && /password|token|image|frames|secret|authorization/i.test(key)) sensitive.push(value);
    else if (Array.isArray(value)) value.forEach((item) => collect(item, key));
    else if (record(value)) Object.entries(value).forEach(([name, item]) => collect(item, name));
  };
  try { collect(typeof error.config?.data === 'string' ? JSON.parse(error.config.data) : error.config?.data); } catch { /* not JSON */ }
  const authorization = error.config?.headers?.Authorization;
  if (typeof authorization === 'string') sensitive.push(authorization, authorization.replace(/^Bearer\s+/i, ''));
  const body: unknown = error.response.data;
  let message: string | undefined;
  let code: string | undefined;
  if (record(body)) {
    code = typeof body.code === 'string' && /^[a-z_]{1,64}$/i.test(body.code) ? body.code : undefined;
    // 5xx bodies may contain infrastructure diagnostics; display stable guidance instead.
    if (status < 500) {
      message = safeMessage(body.message, sensitive) ?? safeMessage(body.detail, sensitive);
      if (!message && Array.isArray(body.detail)) {
        const validation = body.detail.slice(0, 5).map((item: unknown) => {
          if (!record(item)) return undefined;
          return safeMessage(item.msg, sensitive);
        }).filter((value): value is string => !!value);
        if (validation.length) message = Array.from(new Set(validation)).join(' ');
      }
    }
  }
  const retry = Number(error.response.headers?.['retry-after']);
  return {
    kind: 'http', status, code, message: message ?? fallback,
    retryAfterSeconds: Number.isFinite(retry) && retry > 0 ? retry : undefined,
  };
}
