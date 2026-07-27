/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_OFFICE_ENTRA_CLIENT_ID?: string
  readonly VITE_OFFICE_ENTRA_AUTHORITY?: string
  readonly VITE_OFFICE_API_SCOPE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
