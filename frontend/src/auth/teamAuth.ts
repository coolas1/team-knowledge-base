const SESSION_TOKEN_KEY = 'tkb.sessionToken'

export function getSessionToken(): string | null {
  return sessionStorage.getItem(SESSION_TOKEN_KEY)
}

export function setSessionToken(token: string): void {
  sessionStorage.setItem(SESSION_TOKEN_KEY, token)
}

export function clearSessionToken(): void {
  sessionStorage.removeItem(SESSION_TOKEN_KEY)
}

export function hasSessionToken(): boolean {
  return Boolean(getSessionToken())
}

export function getAuthorizationHeader(): string | null {
  const token = getSessionToken()
  return token ? `Bearer ${token}` : null
}
