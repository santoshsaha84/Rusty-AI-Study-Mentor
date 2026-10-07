/**
 * Firebase Auth adapter.
 *
 * Staging/production: the backend returns a Firebase *custom* token at login; the API only
 * accepts Firebase *ID* tokens, so we sign in with the custom token and send the ID token.
 * The Firebase SDK refreshes ID tokens (1 h lifetime) — getFreshIdToken() always returns a
 * valid one.
 *
 * Local development: no VITE_FIREBASE_* config, so the backend's dev tokens are used as-is
 * and the Firebase SDK is never loaded.
 */
import type { Auth } from "firebase/auth";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

export const firebaseEnabled = Boolean(firebaseConfig.apiKey);

let authPromise: Promise<Auth> | null = null;

function getFirebaseAuth(): Promise<Auth> {
  if (!authPromise) {
    authPromise = (async () => {
      const [{ initializeApp }, { initializeAuth, inMemoryPersistence }] = await Promise.all([
        import("firebase/app"),
        import("firebase/auth"),
      ]);
      // In-memory only: nothing survives on shared centre devices once the tab closes,
      // matching the app's own in-memory login state.
      return initializeAuth(initializeApp(firebaseConfig), { persistence: inMemoryPersistence });
    })();
  }
  return authPromise;
}

/** Exchange the login response's custom token for an ID token (pass-through in local dev). */
export async function exchangeLoginToken(customToken: string): Promise<string> {
  if (!firebaseEnabled) return customToken;
  const auth = await getFirebaseAuth();
  const { signInWithCustomToken } = await import("firebase/auth");
  const cred = await signInWithCustomToken(auth, customToken);
  return cred.user.getIdToken();
}

/** Current ID token, refreshed by the SDK when near expiry; falls back to the stored token. */
export async function getFreshIdToken(fallback: string): Promise<string> {
  if (!firebaseEnabled) return fallback;
  const auth = await getFirebaseAuth();
  return auth.currentUser ? auth.currentUser.getIdToken() : fallback;
}

export async function firebaseSignOut(): Promise<void> {
  if (!firebaseEnabled) return;
  const auth = await getFirebaseAuth();
  const { signOut } = await import("firebase/auth");
  await signOut(auth);
}
