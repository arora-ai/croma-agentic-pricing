// Optional sign-in with Microsoft Entra ID (MSAL). It is active ONLY when the app is built with VITE_ENTRA_CLIENT_ID; otherwise nothing in this file
// runs, the MSAL library is never loaded, and `apiFetch` is a plain `fetch`.
//
//   VITE_ENTRA_CLIENT_ID   client id of the single-page-app registration (set this to switch sign-in on)
//   VITE_ENTRA_TENANT_ID   your tenant id (default "organizations": any work or school account)
//   VITE_API_SCOPE         the API's scope, for example api://<api client id>/access_as_user (default: api://<VITE_ENTRA_CLIENT_ID>/access_as_user)
//
// With sign-in on, every API call carries `Authorization: Bearer <access token>`; the API gateway validates it and tells the chat API who the caller is.
// Setting this up: docs/APIM.md, "Entra ID sign-in for production".
import type { IPublicClientApplication } from "@azure/msal-browser";

const clientId: string | undefined = import.meta.env.VITE_ENTRA_CLIENT_ID || undefined;
const tenantId: string = import.meta.env.VITE_ENTRA_TENANT_ID || "organizations";
const scopes: string[] = [import.meta.env.VITE_API_SCOPE || `api://${clientId}/access_as_user`];

export const authActive = Boolean(clientId);

let msal: IPublicClientApplication | null = null;

/** Call once before the app renders. Completes a sign-in redirect; when nobody is signed in it sends the browser to the Microsoft sign-in page. */
export async function initAuth(): Promise<void> {
  if (!clientId) return;
  const { createStandardPublicClientApplication } = await import("@azure/msal-browser");
  msal = await createStandardPublicClientApplication({
    auth: { clientId, authority: `https://login.microsoftonline.com/${tenantId}`, redirectUri: window.location.origin, postLogoutRedirectUri: window.location.origin },
    cache: { cacheLocation: "sessionStorage" },
  });
  const returned = await msal.handleRedirectPromise();
  const account = returned?.account ?? msal.getActiveAccount() ?? msal.getAllAccounts()[0] ?? null;
  if (account) msal.setActiveAccount(account);
  else await msal.loginRedirect({ scopes }); // navigates away to sign in
}

async function accessToken(): Promise<string> {
  if (!msal) throw new Error("Sign-in is not initialised");
  const account = msal.getActiveAccount() ?? msal.getAllAccounts()[0];
  try {
    return (await msal.acquireTokenSilent({ scopes, account })).accessToken;
  } catch (err) {
    const { InteractionRequiredAuthError } = await import("@azure/msal-browser");
    if (!(err instanceof InteractionRequiredAuthError)) throw err;
    await msal.acquireTokenRedirect({ scopes, account }); // the token needs the user again (expired session, new consent): navigates away
    throw err;
  }
}

/** `fetch` that adds the signed-in user's access token when sign-in is on, and is a plain `fetch` when it is off. */
export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  if (!authActive) return fetch(input, init);
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${await accessToken()}`);
  return fetch(input, { ...init, headers });
}

export async function signOut(): Promise<void> {
  await msal?.logoutRedirect();
}
