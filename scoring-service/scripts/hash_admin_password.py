"""Generate a bcrypt hash for ADMIN_PASSWORD_HASH in .env.

Usage (module mode — "python scripts/hash_admin_password.py" fails with
ModuleNotFoundError: No module named 'app', since `app` is only importable
when the project root, not scripts/, is on sys.path):

    uv run python -m scripts.hash_admin_password
    (prompts for a password, hidden input, prints the hash to copy into .env)

    uv run python -m scripts.hash_admin_password 'my-new-password'
    (non-interactive, for scripting — avoid leaving the password in shell history)
"""

import sys
from getpass import getpass

from app.auth import hash_password


def main() -> None:
    password = sys.argv[1] if len(sys.argv) > 1 else getpass("Nieuw admin-wachtwoord: ")
    print(hash_password(password))


if __name__ == "__main__":
    main()
