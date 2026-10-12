"""
Administration commands run on the server. Example:
    docker exec -it patching-backend python -m app.cli reset-password <username>
The new password is typed at a prompt and is never shown on screen or written to a log.
"""

import asyncio
import getpass
import sys

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import User
from app.security import hash_password


async def _reset(username: str, password: str) -> bool:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if user is None:
            return False
        await db.execute(update(User).where(User.id == user.id).values(password_hash=hash_password(password)))
        await db.commit()
        return True


def main(argv):
    if len(argv) != 3 or argv[1] != "reset-password":
        print("usage: python -m app.cli reset-password <username>")
        return 2
    username = argv[2]
    first = getpass.getpass("New password: ")
    if getpass.getpass("Repeat new password: ") != first:
        print("The passwords do not match. Nothing changed.")
        return 1
    if not asyncio.run(_reset(username, first)):
        print(f"No user called {username}. Nothing changed.")
        return 1
    print(f"Password reset for {username}. Sign in with the new password.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
