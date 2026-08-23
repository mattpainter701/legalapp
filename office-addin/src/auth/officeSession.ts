import {
  InteractionRequiredAuthError,
  createNestablePublicClientApplication,
  type IPublicClientApplication,
} from '@azure/msal-browser'

export interface OfficeSessionConfig {
  apiBase: string
  clientId?: string
  authority: string
  apiScope?: string
}

export interface ClaritySessionUser {
  id?: string
  email?: string
  full_name?: string
}

export class OfficeSession {
  private readonly config: OfficeSessionConfig
  private msal: IPublicClientApplication | null = null

  constructor(config: OfficeSessionConfig) {
    this.config = config
  }

  private async currentUser(): Promise<ClaritySessionUser | null> {
    const response = await fetch(`${this.config.apiBase}/auth/me`, {
      credentials: 'include',
      headers: { Accept: 'application/json' },
    })
    if (response.status === 401) return null
    if (!response.ok) throw new Error(`LawHand session check failed (${response.status})`)
    return response.json() as Promise<ClaritySessionUser>
  }

  private async publicClient(): Promise<IPublicClientApplication> {
    if (this.msal) return this.msal
    if (!this.config.clientId || !this.config.apiScope) {
      throw new Error('Office SSO is not configured for this deployment')
    }
    this.msal = await createNestablePublicClientApplication({
      auth: {
        clientId: this.config.clientId,
        authority: this.config.authority,
      },
      cache: {
        cacheLocation: 'sessionStorage',
      },
    })
    return this.msal
  }

  private async acquireAccessToken(): Promise<string> {
    const client = await this.publicClient()
    const request = { scopes: [this.config.apiScope as string] }
    try {
      const response = await client.acquireTokenSilent(request)
      return response.accessToken
    } catch (error) {
      if (!(error instanceof InteractionRequiredAuthError)) throw error
      const response = await client.acquireTokenPopup(request)
      return response.accessToken
    }
  }

  async ensure(naaAvailable: boolean): Promise<ClaritySessionUser> {
    const existing = await this.currentUser()
    if (existing) return existing

    if (!naaAvailable) {
      // The previous message told the user to "sign in to LawHand first", which
      // cannot work: the LawHand session cookie is SameSite=Lax, so it is never
      // sent on this add-in's cross-site request to /auth/me. Signing in, coming
      // back, and retrying produces the identical failure forever. Say what is
      // actually wrong instead of offering an action with no reachable success.
      throw new Error(
        'This version of Office cannot sign in to LawHand. The add-in needs Nested App Authentication, '
        + 'which is available in Microsoft 365 desktop and web clients — perpetual editions such as '
        + 'Office 2016, 2019, and 2021 do not support it. Open this matter in LawHand in your browser, '
        + 'or ask your administrator about a Microsoft 365 client.'
      )
    }

    const accessToken = await this.acquireAccessToken()
    const response = await fetch(`${this.config.apiBase}/auth/office/exchange`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
    })
    if (!response.ok) {
      throw new Error(`Office sign-in could not establish a LawHand session (${response.status})`)
    }
    const user = await this.currentUser()
    if (!user) throw new Error('Office sign-in completed without a LawHand session')
    return user
  }
}
