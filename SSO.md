# Single sign-on

JÁRN can sign people in through an identity provider instead of a local password. It supports two kinds of provider:

- **OpenID Connect:** Microsoft Entra ID, Okta, Google, Keycloak, and any provider that publishes OpenID Connect
  discovery.
- **Active Directory and LDAP:** people type their directory user name and password into JÁRN's sign-in form.

Set everything up in **Admin Settings → Single sign-on**. Client secrets and the directory bind password are stored
encrypted. Once saved, they're never shown again.

## How access works

- A person's **role** comes from the groups the provider sends back: viewer, operator or admin. If someone belongs to
  several mapped groups, they get the highest of those roles.
- Someone who doesn't match any group is refused, unless you set a default role for that provider.
- Every sign-in replaces the account's role with the role the person has at that moment. So a change of groups takes
  effect at the next sign-in.
- SSO accounts have no JÁRN password. Multi-factor authentication is the provider's job. JÁRN's own authenticator-app
  option doesn't apply to these accounts.
- A provider can't take over a local account. If a local account already has the same name, the provider sign-in is
  refused.

**Two things to know before you rely on this for offboarding:**

1. Removing someone from a group doesn't sign them out. Their current session keeps its role until it expires, which
   can be up to seven days for the refresh token. To cut access immediately, disable the account under
   **Admin Settings → Users**. That takes effect on the next request.
2. For Active Directory and LDAP, JÁRN only counts groups the user is a direct member of. Nested groups aren't expanded.

## Before you start

1. Set **Public address of JÁRN** to the address people type into their browser, for example
   `https://jarn.example.com`. Providers send users back to that address, so it has to be reachable from the browser.
   Use HTTPS in production. That also makes the session cookie secure.
2. Copy the **redirect address** shown on the same screen, and register it with each provider. It's always
   `<public address>/api/v1/auth/sso/callback`.
3. Keep at least one local administrator with a password. If your provider goes down, that's the account you use to
   get in. Only turn off password sign-in for local accounts after you've tested sign-in with the provider.

Each provider has a preset. When you add one, the preset fills in the usual user name and group settings for you.

## Microsoft Entra ID

1. In the Entra admin center, go to **App registrations → New registration**. Set the platform to **Web**, and enter
   the redirect address as the redirect URI.
2. Under **Certificates & secrets**, create a new client secret. Copy the value straight away, because Entra only
   shows it once.
3. Under **Token configuration**, add a groups claim and choose security groups. Entra sends group **object IDs**, not
   names, so the mappings in JÁRN need the IDs. You'll find each group's ID on its Overview page.
   - If someone belongs to more than about 200 groups, Entra leaves the list out, and JÁRN refuses the sign-in. To get
     around that, limit which groups the application uses, or use app roles. For app roles, set the groups claim to
     `roles` in JÁRN.
4. In JÁRN, set the issuer to `https://login.microsoftonline.com/<tenant-id>/v2.0`. Enter the application (client) ID
   and the secret. Use `preferred_username` as the username claim and `groups` as the groups claim.

## Okta

1. Under **Applications**, create an App Integration with OIDC and choose **Web application**. The sign-in redirect URI
   is the redirect address. Note the client ID and secret.
2. Under **Security → API → Authorization Servers → default → Claims**, add a claim named `groups`. Include it in the
   ID token, use the Groups value type, and set a filter that matches the groups JÁRN should see.
3. In JÁRN, set the issuer to `https://<your-domain>/oauth2/default`. Use `email` as the username claim, because
   Okta's `preferred_username` is the email address. Use `groups` as the groups claim.

## Google

1. In the Google Cloud Console, set up the OAuth consent screen. Then create an OAuth client ID of type **Web
   application**, and add the redirect address as an authorised redirect URI.
2. In JÁRN, set the issuer to `https://accounts.google.com`. Use `email` as the username claim, and **leave the groups
   claim empty**. Google doesn't send group memberships in the sign-in token.
3. Grant access with the **Allowed email addresses** list, and set a default role for those addresses. Be careful
   here: without a list, any Google account could sign in with the default role. Don't set a default role for Google
   without also listing the addresses.

## Keycloak

1. In your realm, create a client with OpenID Connect. Turn on client authentication (so it's confidential) and the
   standard flow. Add the redirect address as a valid redirect URI.
2. On the client, open the dedicated client scope, go to Mappers, and add a **Group Membership** mapper. Set the token
   claim name to `groups`, leave full group path off, and add the claim to the ID token.
3. In JÁRN, set the issuer to `https://<host>/realms/<realm>`. Use `preferred_username` as the username claim and
   `groups` as the groups claim. Keycloak group names are the keys in the group mapping.

## Any other OpenID Connect provider

Choose **OpenID Connect**. Enter the issuer, which is the address that serves
`/.well-known/openid-configuration`, along with the client ID and secret. Then set the claim names for the user name
and the groups. Press **Test provider** before you turn it on. It checks the discovery document and the signing keys.

## Active Directory and LDAP

1. Create a read-only service account for JÁRN. It needs to read user and group objects in the search base, and nothing
   more.
2. In JÁRN, turn on **Use the directory for sign-in**, and fill in the connection settings:
   - **Server:** the domain controller's name. Use LDAPS on port 636 if you can. STARTTLS on port 389 also works.
   - **Verify the server certificate:** keep this on. The domain controller's certificate authority must be trusted by
     the JÁRN host. Turn verification off only in a lab.
   - **Service account:** its bind DN and password.
   - **Search base**, for example `OU=Staff,DC=example,DC=com`.
   - The defaults are set for Active Directory: the user filter matches `sAMAccountName`, and the group attribute is
     `memberOf`. OpenLDAP doesn't have `memberOf` by default, so use `uid` for the username and set a **group search
     base**.
3. Map groups by their full DN, for example `CN=JARN-Admins,OU=Groups,DC=example,DC=com`. A bare CN also works.
4. Use **Test user lookup** with a real user name. It shows the person's DN, their groups, and the role a sign-in would
   give them. It never asks for or checks a password.

People sign in on JÁRN's own form with their directory password. JÁRN checks it by binding as that user, and it never
stores the password. A wrong password fails, and so does anyone who isn't in a mapped group.

## Testing sign-in

1. Turn on one provider, and leave local sign-in on.
2. Sign out, and use **Sign in with <provider>** with a test account that's in a mapped group.
3. Check that the account appears under **Admin Settings → Users** with the role you expected. Then check that
   **Audit Logs** shows an `auth.sso.login` success.
4. Try an account with no mapped group. The sign-in should be refused, with an explanation.
5. Only then decide whether to turn off password sign-in for local accounts.

Failed sign-ins, replayed or forged sign-in responses, and refused provider logins are all recorded in the audit trail
as `auth.sso.login` failures.
