const CACHE_NAME = 'saiv-static-v1'

const STATIC_ASSETS = [
  '/offline.html',
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
]

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter(
              (key) =>
                key.startsWith('saiv-static-') &&
                key !== CACHE_NAME
            )
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (event) => {
  const request = event.request

  if (
    request.method !== 'GET' ||
    request.mode !== 'navigate'
  ) {
    return
  }

  const url = new URL(request.url)

  if (url.origin !== self.location.origin) {
    return
  }

  // Only handle Next.js student pages. Portal assets, WebSockets and navigation
  // belong to the portal server, including any future configurable portal path.
  if (!['/', '/login', '/register', '/forgot-password', '/dashboard', '/profile'].includes(url.pathname)) {
    return
  }

  event.respondWith(
    fetch(request).catch(async () => {
      const offlinePage = await caches.match('/offline.html')
      return offlinePage || Response.error()
    })
  )
})
