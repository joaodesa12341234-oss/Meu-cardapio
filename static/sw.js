"use strict";
/* Service worker do cardápio: deixa o app instalável e abre mesmo com internet fraca.
   NUNCA guarda /api, /admin nem /acompanhar (sempre dados novos). Troque VERSAO para limpar o cache. */
const VERSAO = "cardapio-v1";
const ESTATICOS = /^\/static\/.+\.(css|js|png|webp|svg)$/;

self.addEventListener("install", (e) => { self.skipWaiting(); });

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((chaves) => Promise.all(chaves.filter((k) => k !== VERSAO).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/admin") || url.pathname.startsWith("/acompanhar")) return;

  if (req.mode === "navigate" && url.pathname === "/") {          // página do cardápio: rede primeiro, cópia se estiver sem internet
    e.respondWith(fetch(req).then((r) => {
      if (r.ok) { const copia = r.clone(); caches.open(VERSAO).then((c) => c.put("/", copia)); }
      return r;
    }).catch(() => caches.match("/").then((r) => r || new Response("Sem conexão. Tente de novo.", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } }))));
    return;
  }
  if (ESTATICOS.test(url.pathname)) {                              // arquivos estáticos: cache primeiro (a URL muda com ?v=)
    e.respondWith(caches.match(req).then((guardado) => guardado || fetch(req).then((r) => {
      if (r.ok) { const copia = r.clone(); caches.open(VERSAO).then((c) => c.put(req, copia)); }
      return r;
    })));
  }
});
