const CACHE = 'pet-paradise-shell-v1';
const SHELL = [
  '/manifest.json',
  '/assets/company_logo.png',
  '/assets/pwa-192.png',
  '/assets/pwa-512.png',
  '/assets/apple-touch-icon.png',
  '/assets/favicon-32.png'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => new Response(`<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#e9475b"><title>Pet Paradise Manager - Offline</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#090d14;color:#f5f7fb;font:16px system-ui}.box{max-width:420px;margin:24px;padding:30px;border:1px solid #293140;border-radius:22px;background:#111722;text-align:center;box-shadow:0 24px 70px #0008}img{width:110px;border-radius:24px}h1{font-size:24px}p{color:#9ca7b8;line-height:1.6}</style></head><body><main class="box"><img src="/assets/pwa-192.png" alt="Pet Paradise"><h1>Sei offline</h1><p>Pet Paradise Manager richiede una connessione per leggere e aggiornare i dati. Riconnettiti e riapri l'app.</p></main></body></html>`, {headers: {'Content-Type': 'text/html; charset=utf-8'}}))
    );
    return;
  }

  if (SHELL.includes(url.pathname)) {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
  }
});
