#!/usr/bin/env python3
"""Проверка полноты словаря без запуска панели.

Ищет в app.js все обращения t('…') и сверяет их со словарём в i18n.js.
Нужна для проверки в CI: там нет ни сервера, ни браузера, а забытый перевод
поймать хочется до слияния.
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def literals(text):
    """Строки внутри t(...) — с учётом экранирования и обеих кавычек."""
    out = []
    for m in re.finditer(r"""\bt\(\s*(['"])((?:\\.|(?!\1).)*)\1\s*\)""", text):
        raw = m.group(2)
        out.append(raw.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\"))
    return out


def dict_keys(text):
    """Ключи словаря DICT: от объявления до закрывающей скобки."""
    start = text.find("const DICT = {")
    if start < 0:
        return set()
    end = text.find("\n};", start)
    body = text[start:end if end > 0 else len(text)]
    keys = set()
    for m in re.finditer(r"""^\s*(['"])((?:\\.|(?!\1).)*)\1\s*:""", body, re.M):
        keys.add(m.group(2).replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\"))
    return keys


def main():
    app = open(os.path.join(BASE, "web", "app.js"), encoding="utf-8").read()
    i18n = open(os.path.join(BASE, "web", "i18n.js"), encoding="utf-8").read()
    used = literals(app)
    known = dict_keys(i18n)
    missing = sorted({s for s in used if s not in known})
    unused = sorted(known - set(used))

    print("строк в интерфейсе: %d, в словаре: %d" % (len(set(used)), len(known)))
    if missing:
        print("\nбез перевода (%d):" % len(missing))
        for s in missing:
            print("   %r" % s)
    if unused:
        print("\nв словаре, но больше не используются (%d):" % len(unused))
        for s in unused[:20]:
            print("   %r" % s)
    if missing:
        return 1
    print("все строки переведены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
