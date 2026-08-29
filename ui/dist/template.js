function d({ apiUrl: c, wsUrl: r, fetchImpl: s = fetch }) {
  async function e() {
    const t = await s(c("/template"));
    if (!t.ok) throw new Error(`GET /template -> ${t.status}`);
    return t.json();
  }
  function a({ onOpen: t, onMessage: p, onClose: l } = {}) {
    const n = new WebSocket(r("/ws/echo"));
    return t && n.addEventListener("open", t), p && n.addEventListener("message", (o) => p(o.data)), l && n.addEventListener("close", l), {
      send: (o) => n.send(o),
      close: () => n.close(),
      raw: n
    };
  }
  return { template: e, connectEcho: a };
}
const i = "aw-app-template";
function f(c) {
  const s = d({
    apiUrl: (e) => `/api/apps/${i}${e}`,
    wsUrl: (e) => c.sdk.api.wsUrl(`/api/apps/${i}${e}`),
    fetchImpl: (e, a) => c.sdk.api.fetch(e, a)
  }).connectEcho({
    onMessage: (e) => console.debug(`[${i}] echo:`, e)
  });
  c.onDispose(() => s.close());
}
export {
  f as default,
  f as register
};
