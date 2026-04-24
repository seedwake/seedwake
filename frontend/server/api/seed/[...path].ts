// Proxy /api/seed/* → backend /api/* with X-API-Token injected server-side.
// Token never reaches the browser.

import { defineEventHandler, getRequestURL, proxyRequest } from "h3";

export default defineEventHandler(async (event) => {
  const config = useRuntimeConfig();
  const base = String(config.backendBaseUrl || "").replace(/\/$/, "");
  const token = String(config.backendApiToken || "");

  const url = getRequestURL(event);
  const suffix = url.pathname.replace(/^\/api\/seed\/?/, "");
  const target = `${base}/api/${suffix}${url.search}`;

  return proxyRequest(event, target, {
    headers: {
      "X-API-Token": token,
    },
    sendStream: true,
  });
});
