#!/usr/bin/env python3
"""Задать логин и пароль для входа в панель:
    python3 tools/passwd.py <логин> [пароль]
Без пароля в аргументах он запрашивается скрытым вводом — так он не попадёт
ни в историю команд, ни в список процессов."""
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
        password = getpass.getpass("Пароль для %s: " % login)
        if password != getpass.getpass("Ещё раз: "):
            print("Пароли не совпали")
            return 1
    if len(password) < 8:
        print("Слишком короткий пароль: нужно хотя бы 8 символов")
        return 1
    store.init()
    auth.set_user(login, password)
    print("Пользователь %s сохранён в var/users.json (только хеш пароля)" % login)
    return 0

if __name__ == "__main__":
    sys.exit(main())
