import { safePortalDestination } from './portal-navigation';

export interface PublicConfiguration {
  apiUrl: string;
  staffPortalUrl: string;
  studentPortalUrl?: string;
  studentPortalLocalOnly?: boolean;
  apiViaFrontend?: boolean;
}

function parsePublicUrl(value: string, name: string): URL {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) ||
      url.username || url.password || url.search || url.hash) {
    throw new Error(`${name} must be an HTTP(S) URL without credentials, query or fragment.`);
  }
  return url;
}

/** Strip trailing slashes and repeated API prefixes from a base path. */
function normalizeBasePath(pathname: string): string {
  const path = pathname.replace(/\/+$/, '').replace(/(?:\/api\/v1)+$/, '');
  if (path.includes('/api/v1')) {
    throw new Error('The API URL must end at the /api/v1 base, not an API resource.');
  }
  return path;
}

/** Accept an origin or deployment base, with zero or repeated trailing API prefixes.
 *
 * A root-relative value such as '/api/v1' is also accepted, and is what keeps
 * API calls same-origin: the browser sends them to whichever host served the
 * page and the rewrite in next.config.js forwards them to the backend. That is
 * what lets one build serve localhost, a dev tunnel and a real domain without
 * an ALLOWED_ORIGINS entry per host, and without the backend being reachable
 * from the browser at all. A protocol-relative '//host' is NOT relative: it
 * falls through and is rejected, so it cannot redirect the API off-origin.
 */
export function normalizeApiBaseUrl(value = 'http://localhost:8000/api/v1'): string {
  const raw = value.trim() || 'http://localhost:8000/api/v1';
  if (raw.startsWith('/') && !raw.startsWith('//')) {
    if (raw.includes('?') || raw.includes('#')) {
      throw new Error('API URL must be an HTTP(S) URL without credentials, query or fragment.');
    }
    return `${normalizeBasePath(raw)}/api/v1`;
  }
  const url = parsePublicUrl(raw, 'API URL');
  return `${url.origin}${normalizeBasePath(url.pathname)}/api/v1`;
}

export function normalizeApiPath(path: string): string {
  // Do not let an absolute URL bypass the configured backend.
  if (!path.startsWith('/') || path.startsWith('//')) {
    throw new Error('API requests must use a root-relative resource path.');
  }
  return path.replace(/^(?:\/api\/v1)+(?=\/|$)/, '') || '/';
}

export function resolvePublicConfiguration(
  environment: Record<string, string | undefined>
): PublicConfiguration {
  const staffPortalUrl = parsePublicUrl(
    environment.NEXT_PUBLIC_STAFF_PORTAL_URL?.trim() || 'http://localhost:8501',
    'Staff portal URL'
  ).href;
  // Public HTTPS integration stays off until Module 4's URL-session issue is fixed.
  // A separate opt-in permits local browser access without opening that gateway.
  const studentPortalUrl = environment.SAIV_STUDENT_PORTAL_ENABLED === 'true'
    ? safePortalDestination(environment.NEXT_PUBLIC_STUDENT_PORTAL_URL?.trim()) : null;
  return {
    apiUrl: normalizeApiBaseUrl(environment.NEXT_PUBLIC_API_URL),
    staffPortalUrl,
    ...(environment.SAIV_BACKEND_INTERNAL_URL ? { apiViaFrontend: true } : {}),
    ...(studentPortalUrl ? { studentPortalUrl } : {}),
    ...(studentPortalUrl && environment.SAIV_STUDENT_PORTAL_LOCAL_ONLY === 'true' ? { studentPortalLocalOnly: true } : {}),
  };
}
