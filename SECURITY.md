# Security policy

## Supported versions

Security fixes go to the latest release and to `main`. Older releases don't get fixes; upgrade to the latest.

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately through GitHub: open the repository's
**Security** tab and choose **Report a vulnerability**. Include what you found, how to reproduce it, and the version
or commit you tested.

You'll get an acknowledgement within a week. Fixes are prepared privately and released with a note describing the
problem and its impact. Credit goes to the reporter unless you'd rather stay anonymous.

## Scope

In scope: the JÁRN backend, the web dashboard, the nginx configuration, the container images and the deployment files
in this repository. Out of scope: the servers JÁRN manages, your NAS or Proxmox host, and third-party services.

Never put real credentials, private keys or server addresses in a report. Describe the problem and we'll arrange a
safe way to share details.
