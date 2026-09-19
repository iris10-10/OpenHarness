export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json() as Promise<T>;
}

export async function apiGet<T>(path: string): Promise<T> {
  return parse<T>(await fetch(`${API_BASE}${path}`));
}

export async function apiSend<T>(path: string, init: RequestInit): Promise<T> {
  return parse<T>(
    await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: init.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...(init.headers || {}) },
    }),
  );
}
