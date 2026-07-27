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
    if (!response.ok) throw new Error(`Clarity session check failed (${response.status})`)
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
      throw new Error('Sign in to Clarity first, or open this add-in in an Office client that supports Nested App Authentication')
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
      throw new Error(`Office sign-in could not establish a Clarity session (${response.status})`)
    }
    const user = await this.currentUser()
    if (!user) throw new Error('Office sign-in completed without a Clarity session')
    return user
  }
}
