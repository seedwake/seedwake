// Seedwake frontend — Nuxt 4
export default defineNuxtConfig({
  compatibilityDate: "2025-11-01",
  devtools: { enabled: true },
  ssr: true,

  modules: ["@nuxtjs/i18n"],

  runtimeConfig: {
    backendBaseUrl: "http://127.0.0.1:8000",
    backendApiToken: "",
    public: {
      language: "zh",
    },
  },

  i18n: {
    strategy: "no_prefix",
    defaultLocale: "zh",
    locales: [
      { code: "zh", name: "中文", language: "zh-Hans", file: "zh.ts" },
      { code: "en", name: "English", language: "en", file: "en.ts" },
    ],
    detectBrowserLanguage: {
      useCookie: true,
      cookieKey: "seedwake_lang",
      fallbackLocale: "zh",
      redirectOn: "root",
    },
  },

  css: ["~/assets/styles/tokens.css", "~/assets/styles/components.css"],

  app: {
    head: {
      title: "Seedwake · 心相续",
      link: [
        { rel: "preconnect", href: "https://fonts.googleapis.com" },
        { rel: "preconnect", href: "https://fonts.gstatic.com", crossorigin: "" },
        {
          rel: "stylesheet",
          href: "https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@300;400;500;600&family=Noto+Sans+SC:wght@300;400;500&family=JetBrains+Mono:wght@300;400;500&family=EB+Garamond:ital,wght@0,400;0,500;1,400&family=Fraunces:ital,wght@0,400;0,500;1,400&family=Inter:wght@300;400;500&family=Ma+Shan+Zheng&display=swap",
        },
      ],
    },
  },

  nitro: {
    preset: "node-server",
  },
});
