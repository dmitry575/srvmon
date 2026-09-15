#!/usr/bin/env python3
"""Set the login and password for the dashboard.

    python3 tools/passwd.py <login> [password]

With no password in the arguments it is asked for interactively, so it lands
neither in the shell history nor in the process list.
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import auth, store  # noqa: E402

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    login = sys.argv[1]
    if len(sys.argv) > 2:
        password = sys.argv[2]
    else:
        password = getpass.getpass("Password for %s: " % login)
        if password != getpass.getpass("Repeat: "):
            print("Passwords do not match")
            return 1
    if len(password) < 8:
        print("Password too short: at least 8 characters")
        return 1
    store.init()
    auth.set_user(login, password)
    print("User %s saved to var/users.json (password hash only)" % login)
    return 0

if __name__ == "__main__":
    sys.exit(main())
