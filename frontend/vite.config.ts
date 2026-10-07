/// <reference types="vitest" />
import { defineConfig, loadEnv, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// Fills the CSP placeholder in index.html with the API origin (VITE_API_URL). Locally the API
// is same-origin through the /api proxy, so nothing is added.
function cspApiOrigin(apiUrl: string | undefined): Plugin {
  const origin = apiUrl && /^https?:\/\//.test(apiUrl) ? new URL(apiUrl).origin : "";
  return {
    name: "rusty-csp-api-origin",
    transformIndexHtml: (html) => html.replace("__API_ORIGIN__", origin),
  };
}

export default defineConfig(({ mode }) => ({
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
  plugins: [
    react(),
    // loadEnv reads .env.[mode] files and shell variables (shell wins), like the app build does
    cspApiOrigin(loadEnv(mode, process.cwd(), "VITE_").VITE_API_URL),
    VitePWA({
      registerType: "autoUpdate",
      workbox: {
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
        runtimeCaching: [
          {
            urlPattern: /^https:\/\/fonts\.gstatic\.com/,
            handler: "CacheFirst",
            options: {
              cacheName: "google-fonts",
              expiration: { maxEntries: 30, maxAgeSeconds: 60 * 60 * 24 * 365 },
            },
          },
          {
            urlPattern: /\.(?:js|css|woff2|png|svg|ico)$/,
            handler: "StaleWhileRevalidate",
            options: {
              cacheName: "static-assets",
              expiration: { maxEntries: 100, maxAgeSeconds: 60 * 60 * 24 * 30 },
            },
          },
        ],
      },
      manifest: {
        name: "Rusty — Study Mentor",
        short_name: "Rusty",
        theme_color: "#2D6A4F",
        background_color: "#FFF8F0",
        display: "standalone",
        icons: [
          { src: "/icon-192.svg", sizes: "192x192", type: "image/svg+xml" },
          { src: "/icon-512.svg", sizes: "512x512", type: "image/svg+xml" },
        ],
      },
    }),
  ],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
}));
