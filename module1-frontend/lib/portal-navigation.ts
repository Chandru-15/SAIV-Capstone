/** Credential-free configured destinations only. Never derives a destination from user/session data. */
export function safePortalDestination(value: unknown, browserOrigin?: string, localDevelopmentOnly = false): string | null {
  if (typeof value !== 'string' || !value || /[\s\\]/.test(value)) return null;
  const relative = value.startsWith('/') && !value.startsWith('//');
  try {
    const url = new URL(value, browserOrigin || 'https://saiv-config.invalid');
    if (!relative && !/^https?:\/\//i.test(value)) return null;
    if (url.username || url.password || url.search || url.hash) return null;
    if (localDevelopmentOnly) {
      const allowed = (host: string) => host === 'localhost' || host === '127.0.0.1';
      if (!browserOrigin || !allowed(new URL(browserOrigin).hostname) || !allowed(url.hostname) || relative) return null;
    }
    const local = (host: string) => ['localhost', '127.0.0.1', '[::1]'].includes(host);
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local(url.hostname))) return null;
    if (browserOrigin) {
      const browser = new URL(browserOrigin);
      if (browser.protocol === 'https:' && url.protocol !== 'https:') return null;
      if (local(url.hostname) && !local(browser.hostname)) return null;
    }
    if ((relative || browserOrigin === url.origin) && ['/', '/dashboard', '/profile', '/login', '/register', '/forgot-password'].includes(url.pathname.replace(/\/$/, '') || '/')) return null;
    return relative ? `${url.pathname}` : url.href;
  } catch { return null; }
}
