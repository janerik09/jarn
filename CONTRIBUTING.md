# Contributing to JÁRN

Thanks for helping. Bug reports, fixes and documentation changes are all welcome.

## Before you start

- For a larger change, open an issue first so we can agree on the approach.
- Security problems don't go in issues. See [SECURITY.md](SECURITY.md).
- Windows servers are out of scope; JÁRN manages Linux servers only.

## Setting up

[DEVELOPMENT.md](DEVELOPMENT.md) covers the development stack, the backend tests and the frontend build. Run them
before you open a pull request:

- Backend: the test suite in `backend/`, with `pytest -q`. Some lock tests need Redis.
- Frontend: `npm run build` in `frontend-web/`, which also type-checks.

## Pull requests

- Keep each pull request to one change, with a clear description of what and why.
- Add tests for backend changes. A bug fix should come with a test that fails without the fix.
- Update the guides in the same pull request when behaviour or settings change.
- Never commit secrets, `.env` files, certificates, database dumps or real server details. Use example values such
  as `192.0.2.10` (a documentation address) or `example.com`.

## Developer Certificate of Origin

Every commit needs a sign-off, which states that you wrote the change or may submit it under the project licence.
Add it with `git commit -s`, which appends a line like:

    Signed-off-by: Your Name <you@example.com>

By signing off you agree to the [Developer Certificate of Origin](https://developercertificate.org/).

## Licence

JÁRN is licensed under the Apache License 2.0 (see [LICENSE](LICENSE)). Your contributions are licensed the same way.
