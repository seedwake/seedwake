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
      demo: false,
    },
  },

  i18n: {
    strategy: "no_prefix",
    defaultLocale: "zh",
    locales: [
      { code: "zh", name: "中文", language: "zh-Hans" },
      { code: "en", name: "English", language: "en" },
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
        // Modern browsers prefer SVG (crisp at any DPI). Fallback chain below
        // covers older Safari, Windows pinned tiles, iOS home screen, and PWA
        // installs.
        { rel: "icon", type: "image/svg+xml", href: "/favicon.svg" },
        { rel: "icon", type: "image/png", sizes: "96x96", href: "/icon-96x96.png" },
        { rel: "icon", type: "image/x-icon", href: "/favicon.ico" },
        { rel: "apple-touch-icon", sizes: "180x180", href: "/apple-touch-icon.png" },
        { rel: "manifest", href: "/manifest.webmanifest" },
        { rel: "preconnect", href: "https://fonts.googleapis.com" },
        { rel: "preconnect", href: "https://fonts.gstatic.com", crossorigin: "" },
        {
          rel: "stylesheet",
          href: "https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@300;400;500;600&family=Noto+Sans+SC:wght@300;400;500&family=JetBrains+Mono:wght@300;400;500&family=EB+Garamond:ital,wght@0,400;0,500;1,400&family=Fraunces:ital,wght@0,400;0,500;1,400&family=Inter:wght@300;400;500&family=Ma+Shan+Zheng&display=swap",
        },
      ],
      meta: [
        // Matches manifest.webmanifest's theme_color so address bar / tab UI
        // tints to paper cream when supported.
        { name: "theme-color", content: "#FAF8F3" },
      ],
    },
  },

  nitro: {
    preset: "node-server",
  },
});
