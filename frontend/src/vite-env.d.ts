/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API base URL. Unset locally (Vite proxies /api); the Cloud Run URL on GCP. */
  readonly VITE_API_URL?: string;
  /** Firebase web config (public values). Unset locally — dev tokens bypass Firebase. */
  readonly VITE_FIREBASE_API_KEY?: string;
  readonly VITE_FIREBASE_AUTH_DOMAIN?: string;
  readonly VITE_FIREBASE_PROJECT_ID?: string;
  readonly VITE_FIREBASE_APP_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
