import { getFreshIdToken } from "./firebase";

const BASE_URL = import.meta.env.VITE_API_URL || "/api";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  options: RequestInit & { token?: string; signal?: AbortSignal } = {},
): Promise<T> {
  const { token, signal, ...fetchOptions } = options;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(fetchOptions.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${await getFreshIdToken(token)}`;

  const res = await fetch(`${BASE_URL}${path}`, { ...fetchOptions, headers, signal });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: "Unknown error" }));
    throw new ApiError(res.status, body.detail ?? "Request failed");
  }

  return res.json() as Promise<T>;
}

export const api = {
  post: <T>(path: string, body: unknown, token?: string, signal?: AbortSignal) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body), token, signal }),

  get: <T>(path: string, token?: string) =>
    request<T>(path, { method: "GET", token }),

  delete: <T>(path: string, token?: string) =>
    request<T>(path, { method: "DELETE", token }),

  postForm: <T>(path: string, formData: FormData, token?: string) =>
    request<T>(path, {
      method: "POST",
      body: formData,
      token,
      headers: {} as Record<string, string>, // let browser set multipart boundary
    }),
};

export type SSEEvent = {
  event: string;
  data: unknown;
};

export function streamSSE(
  path: string,
  body: unknown,
  token: string,
  onEvent: (evt: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise(async (resolve, reject) => {
    try {
      const res = await fetch(`${BASE_URL}${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${await getFreshIdToken(token)}`,
        },
        body: JSON.stringify(body),
        signal,
      });

      if (!res.ok) {
        const errBody = await res.json().catch(() => ({ detail: "Unknown error" }));
        reject(new ApiError(res.status, errBody.detail ?? "Request failed"));
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) { resolve(); return; }

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const part of parts) {
          const lines = part.trim().split("\n");
          let eventName = "message";
          let dataStr = "";
          for (const line of lines) {
            if (line.startsWith("event: ")) eventName = line.slice(7);
            else if (line.startsWith("data: ")) dataStr = line.slice(6);
          }
          if (dataStr) {
            try {
              onEvent({ event: eventName, data: JSON.parse(dataStr) });
            } catch { /* skip malformed */ }
          }
        }
      }
      resolve();
    } catch (err) {
      if (signal?.aborted) resolve();
      else reject(err);
    }
  });
}

export { ApiError };
