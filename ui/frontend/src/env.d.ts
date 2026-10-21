/// <reference types="vite/client" />

// Build-time settings for the optional Entra ID sign-in (see auth.ts). All are optional; without VITE_ENTRA_CLIENT_ID there is no sign-in.
interface ImportMetaEnv {
  readonly VITE_ENTRA_CLIENT_ID?: string;
  readonly VITE_ENTRA_TENANT_ID?: string;
  readonly VITE_API_SCOPE?: string;
}
