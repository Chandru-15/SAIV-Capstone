import { normalizeApiBaseUrl, resolvePublicConfiguration } from './public-config';
import type { PublicConfiguration } from './public-config';

let configuration: Promise<PublicConfiguration> | undefined;

/** Shared by concurrent requests; failed loads can be retried. Contains no secrets. */
export function getPublicConfiguration(): Promise<PublicConfiguration> {
  if (!configuration) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    configuration = fetch('/api/config', {
          cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
        })
      .then(async (response) => {
        if (!response.ok) throw new Error('Unable to load application configuration.');
        const body: unknown = await response.json();
        if (!body || typeof body !== 'object' ||
            !('apiUrl' in body) || typeof body.apiUrl !== 'string' ||
            !('staffPortalUrl' in body) || typeof body.staffPortalUrl !== 'string') {
          throw new Error('Invalid application configuration.');
        }
        const resolved = resolvePublicConfiguration({
          NEXT_PUBLIC_API_URL: body.apiUrl,
          NEXT_PUBLIC_STAFF_PORTAL_URL: body.staffPortalUrl,
          NEXT_PUBLIC_STUDENT_PORTAL_URL: 'studentPortalUrl' in body && typeof body.studentPortalUrl === 'string' ? body.studentPortalUrl : undefined,
          SAIV_STUDENT_PORTAL_ENABLED: 'studentPortalUrl' in body ? 'true' : undefined,
          SAIV_STUDENT_PORTAL_LOCAL_ONLY: 'studentPortalLocalOnly' in body && body.studentPortalLocalOnly === true ? 'true' : undefined,
        });
        // An explicitly configured Next.js proxy also serves local review. The
        // gateway handles these same paths on phones; no tunnel cookies/CORS
        // are needed merely to review the application from localhost.
        if ('apiViaFrontend' in body && body.apiViaFrontend === true && window.location?.origin) {
          return { ...resolved, apiUrl: normalizeApiBaseUrl(window.location.origin) };
        }
        return resolved;
      })
      .catch((error: unknown) => {
        configuration = undefined;
        throw error;
      })
      .finally(() => clearTimeout(timeout));
  }
  return configuration;
}
