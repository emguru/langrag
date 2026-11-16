import { getIdToken, signOut } from "./auth";

export class AuthRequiredError extends Error {
  constructor() {
    super("Please sign in again.");
    this.name = "AuthRequiredError";
  }
}

export async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getIdToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const resp = await fetch(input, { ...init, headers });
  if (resp.status === 401) {
    signOut();
    throw new AuthRequiredError();
  }
  return resp;
}
