// Resolve an I18nTextPayload {key, params} against the frontend's i18n catalog,
// falling back to the raw key if the catalog doesn't know it.

import type { I18nTextPayload } from "~/types/api";

export function useI18nText() {
  const { t, te } = useI18n();
  return function resolve(payload: I18nTextPayload | string | undefined | null): string {
    if (!payload) return "";
    if (typeof payload === "string") return payload;
    if (!payload.key) return "";
    return te(payload.key)
      ? (t(payload.key, payload.params || {}) as string)
      : payload.key;
  };
}
