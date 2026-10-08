// Civic Lens: ニュースの見張りの通知（Web Push）を受け取る Service Worker
// 通知の先は、保存済みの検討を見せるだけ。開示請求・SNS投稿の判断は本人が行う。

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = {}; }
  const title = data.title || '🤖 AIエージェントが注目しています';
  const options = {
    body: data.body || '',
    icon: '/static/favicon-192x192.png',
    badge: '/static/favicon-192x192.png',
    tag: data.link || 'civic-lens-watch',
    data: { link: data.link || '', title: data.title_raw || '' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const d = event.notification.data || {};
  const q = d.link ? `/?watch_link=${encodeURIComponent(d.link)}&watch_title=${encodeURIComponent(d.title || '')}` : '/';
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) {
      if ('focus' in c) {
        await c.focus();
        if ('navigate' in c) await c.navigate(q);
        return;
      }
    }
    await self.clients.openWindow(q);
  })());
});
