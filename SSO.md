# Single sign-on

JÁRN can sign users in with an identity provider instead of a local password. Supported:

- **OpenID Connect**: Microsoft Entra ID, Okta, Google, Keycloak, and any provider that publishes OpenID Connect discovery
- **Active Directory and LDAP**: users type their directory user name and password into JÁRN's sign-in form

Configure everything in **Admin Settings → Single sign-on**. Secrets (client secrets, the directory bind password) are
stored encrypted and are never shown again after saving.

## How access works

- A user's **role** comes from the groups the provider reports: viewer, operator or admin. If a user is in several mapped
  groups, the highest role applies.
- A user who matches no group is **refused**, unless you set a **default role** for the provider.
- Each sign-in **replaces** the account's role with the one it earns, so a change of groups takes effect at the next sign-in.
- SSO accounts have no JÁRN password. Multi-factor authentication is the provider's job: JÁRN's own authenticator-app
  option does not apply to them.
- Local accounts cannot be taken over by a provider. If a local account has the same name, the provider sign-in is refused.

**Two things to know before you rely on it for offboarding:**

1. A removed group does not sign the user out. Their session keeps its role until it expires (up to seven days for the
   refresh token). To remove access at once, **disable the account** in **Admin Settings → Users**. Disabling takes effect
   at the next request.
2. Directory membership is read only for groups the user is a direct member of. Nested groups are not expanded.

## Before you start

1. Set **Public address of JÁRN** to the address people type in their browser, for example `https://jarn.example.com`.
   Providers redirect back to it, so it must be reachable from the user's browser. Use HTTPS in production, so the session
   cookie is marked secure.
2. Note the **redirect address** shown on the same screen. Register it with each provider. It is always
   `<public address>/api/v1/auth/sso/callback`.
3. Keep a local administrator with a password. If the provider is down, that account is how you get in. Set
   **Allow password sign-in for local accounts** to off only after you have tested sign-in with the provider.

## Per provider

Use the preset when you add an option. It fills in the usual username and group settings.

### Microsoft Entra ID

1. In the Entra admin center, **App registrations → New registration**. Redirect URI: type **Web**, value = the redirect
   address above.
2. **Certificates & secrets → New client secret**. Copy the secret value now; Entra shows it once.
3. **Token configuration → Add groups claim**. Choose security groups. Entra sends group **object IDs**, so the group
   mappings in JÁRN must be the group IDs (shown on each group's Overview page).
   - If a user belongs to more than about 200 groups, Entra omits the list and JÁRN refuses the sign-in. Limit the groups
     the application uses, or use app roles (set the groups claim to `roles` in JÁRN).
4. In JÁRN: issuer `https://login.microsoftonline.com/<tenant-id>/v2.0`, client id = the application (client) ID, the secret,
   username claim `preferred_username`, groups claim `groups`.

### Okta

1. **Applications → Create App Integration → OIDC → Web application**. Sign-in redirect URI = the redirect address above.
   Note the client ID and secret.
2. **Security → API → Authorization Servers → default → Claims → Add Claim**: name `groups`, include in ID token, value type
   Groups, filter that matches the groups JÁRN should see.
3. In JÁRN: issuer `https://<your-domain>/oauth2/default` (the authorisation server issuer), username claim `email` (Okta's
   `preferred_username` is the email address), groups claim `groups`.

### Google

1. Google Cloud Console → **APIs & Services → OAuth consent screen**, then **Credentials → Create credentials → OAuth client
   ID → Web application**. Authorised redirect URI = the redirect address above.
2. In JÁRN: issuer `https://accounts.google.com`, username claim `email`, and **leave the groups claim empty**. Google does not
   send group memberships in the sign-in token.
3. Grant access by listing **Allowed email addresses**, and set a default role for them. Without an allow-list, every Google
   account on the internet could sign in with the default role, so do not leave the default set without a list.

### Keycloak

1. In the realm, **Clients → Create client**: OpenID Connect, client authentication on (confidential), standard flow on.
   Valid redirect URI = the redirect address above.
2. On the client, **Client scopes → dedicated scope → Mappers → Add mapper → Group Membership**: token claim name `groups`,
   full group path off, add to ID token on.
3. In JÁRN: issuer `https://<host>/realms/<realm>`, username claim `preferred_username`, groups claim `groups`.
   Keycloak group names are used as the mapping keys.

### Any other OpenID Connect provider

Choose **OpenID Connect**. Enter the issuer (the address that serves `/.well-known/openid-configuration`), the client
ID and secret, and the claim names for the user name and groups. Use **Test provider** to check the discovery document and
signing keys before you enable it.

## Active Directory and LDAP

1. Create a **read-only service account** for JÁRN. Give it read access to user and group objects in the search base. Nothing else.
2. In JÁRN, turn on **Use the directory for sign-in**:
   - Server: the domain controller name, and LDAPS on port 636 (recommended). STARTTLS on 389 is supported too.
   - Keep **Verify the server certificate** on. The domain controller's CA must be trusted by the JÁRN host. For a lab only,
     you can turn verification off.
   - Service account DN and password.
   - Search base, for example `OU=Staff,DC=example,DC=com`.
   - Active Directory defaults are filled in: user filter on `sAMAccountName`, group attribute `memberOf`.
     For OpenLDAP, use `uid` and set **Group search base**, since OpenLDAP has no `memberOf` by default.
3. Map groups by their full DN, for example `CN=JARN-Admins,OU=Groups,DC=example,DC=com`. A bare CN also works.
4. Use **Test user lookup** with a real user name. It shows the user's DN, groups and the role that would be granted.
   It never asks for or checks a password.

Users sign in on JÁRN's own form with their directory password. JÁRN checks it against the directory by binding as the
user, and never stores it. A user whose password is wrong, or who is not in a mapped group, cannot sign in.

## Testing sign-in

1. Enable one provider, and keep local sign-in on.
2. Sign out, and use **Sign in with <provider>** with a test account in a mapped group.
3. Check the account appears in **Admin Settings → Users** with the expected role and that **Audit Logs** shows
   `auth.sso.login` as a success.
4. Try a user with no mapped group. The sign-in must be refused with an explanation.
5. Only then turn off local sign-in, if you want to.

Failed sign-ins, replayed or forged sign-in responses, and refused provider logins are recorded in the audit trail as
`auth.sso.login` failures.
