// Thin wrapper over $fetch for the Nitro proxy.

export function useApi() {
  const base = "/api/seed";
  return {
    async get<T>(path: string, query?: Record<string, string | number>): Promise<T> {
      return await $fetch<T>(`${base}${path}`, { method: "GET", query });
    },
    async post<T>(path: string, body?: Record<string, unknown>): Promise<T> {
      return await $fetch<T>(`${base}${path}`, { method: "POST", body });
    },
    streamUrl(path: string): string {
      return `${base}${path}`;
    },
  };
}
