#!/usr/bin/env python3
"""Dictionary completeness check that needs no running dashboard.

Collects every t('…') call in app.js and matches it against the dictionary in
i18n.js. Written for CI, where there is neither a server nor a browser, but a
forgotten translation should still be caught before a merge.
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def literals(text):
    """Strings inside t(...), honouring escapes and both quote styles."""
    out = []
    for m in re.finditer(r"""\bt\(\s*(['"])((?:\\.|(?!\1).)*)\1\s*\)""", text):
        raw = m.group(2)
        out.append(raw.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\"))
    return out


def dict_keys(text):
    """Keys of the DICT object: from its declaration to the closing brace."""
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

    print("interface strings: %d, dictionary entries: %d" % (len(set(used)), len(known)))
    if missing:
        print("\nmissing translations (%d):" % len(missing))
        for s in missing:
            print("   %r" % s)
    if unused:
        print("\nin the dictionary but no longer used (%d):" % len(unused))
        for s in unused[:20]:
            print("   %r" % s)
    if missing:
        return 1
    print("every string is translated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
