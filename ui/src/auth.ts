const SESSION_KEY = "rag-auth";

export type AuthConfig = {
  userPoolId: string;
  clientId: string;
  region: string;
};

export type AuthSession = {
  idToken: string;
  accessToken: string;
  refreshToken: string;
  username: string;
};

type CognitoAuthResult = {
  AuthenticationResult?: {
    IdToken?: string;
    AccessToken?: string;
    RefreshToken?: string;
  };
  ChallengeName?: string;
  Session?: string;
  message?: string;
  __type?: string;
};

export function authRequired(config: AuthConfig | null): boolean {
  return Boolean(config?.userPoolId && config.clientId);
}

export function readSession(): AuthSession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AuthSession;
    if (!parsed.idToken) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function writeSession(session: AuthSession | null): void {
  if (!session) {
    sessionStorage.removeItem(SESSION_KEY);
    return;
  }
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function getIdToken(): string | null {
  return readSession()?.idToken ?? null;
}

export async function loadAuthConfig(): Promise<AuthConfig | null> {
  try {
    const resp = await fetch("/auth-config.json", { cache: "no-store" });
    if (!resp.ok) return null;
    const data = (await resp.json()) as AuthConfig;
    if (!data.userPoolId || !data.clientId) return null;
    return data;
  } catch {
    return null;
  }
}

export async function signIn(
  config: AuthConfig,
  username: string,
  password: string,
): Promise<{ session?: AuthSession; newPasswordRequired?: { session: string; username: string } }> {
  const data = await cognitoCall(config.region, "InitiateAuth", {
    AuthFlow: "USER_PASSWORD_AUTH",
    ClientId: config.clientId,
    AuthParameters: {
      USERNAME: username.trim(),
      PASSWORD: password,
    },
  });
  if (data.ChallengeName === "NEW_PASSWORD_REQUIRED" && data.Session) {
    return { newPasswordRequired: { session: data.Session, username: username.trim() } };
  }
  return { session: sessionFromResult(data, username.trim()) };
}

export async function completeNewPassword(
  config: AuthConfig,
  username: string,
  newPassword: string,
  challengeSession: string,
): Promise<AuthSession> {
  const data = await cognitoCall(config.region, "RespondToAuthChallenge", {
    ChallengeName: "NEW_PASSWORD_REQUIRED",
    ClientId: config.clientId,
    Session: challengeSession,
    ChallengeResponses: {
      USERNAME: username,
      NEW_PASSWORD: newPassword,
    },
  });
  return sessionFromResult(data, username);
}

export function signOut(): void {
  writeSession(null);
}

function sessionFromResult(data: CognitoAuthResult, username: string): AuthSession {
  const result = data.AuthenticationResult;
  if (!result?.IdToken || !result.AccessToken) {
    throw new Error(data.message || "Sign-in did not return a session.");
  }
  return {
    idToken: result.IdToken,
    accessToken: result.AccessToken,
    refreshToken: result.RefreshToken || readSession()?.refreshToken || "",
    username,
  };
}

async function cognitoCall(
  region: string,
  target: "InitiateAuth" | "RespondToAuthChallenge",
  body: Record<string, unknown>,
): Promise<CognitoAuthResult> {
  const resp = await fetch(`https://cognito-idp.${region}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": `AWSCognitoIdentityProviderService.${target}`,
    },
    body: JSON.stringify(body),
  });
  const data = (await resp.json()) as CognitoAuthResult;
  if (!resp.ok) {
    throw new Error(humanCognitoError(data));
  }
  return data;
}

function humanCognitoError(data: CognitoAuthResult): string {
  const type = data.__type || "";
  if (type.includes("NotAuthorizedException")) return "Email or password is wrong.";
  if (type.includes("UserNotFoundException")) return "No account for that email.";
  if (type.includes("PasswordResetRequiredException")) return "This account needs a new password.";
  if (type.includes("InvalidPasswordException")) {
    return "Password must be 12+ characters with upper, lower, number, and symbol.";
  }
  return data.message || "Could not sign in.";
}
