# Contributing

Thanks for looking. This is a small project with a narrow purpose, and the
easiest way to help is to use it on a server that is not the one it was built
on, and say what broke.

## Reporting something

Useful bug reports contain: what you did, what you expected, what happened,
plus the output of

```bash
systemctl status srvmon srvmon-collector
journalctl -u srvmon-collector -n 50
python3 -c "import sys; print(sys.version)"
```

If it involves a site, a database or a certificate, say which kind — nginx or
something else, MySQL or PostgreSQL, Let's Encrypt or self-signed. Most bugs
found so far came from a setup that differed from the author's in exactly one
detail.

## Changing something

The two rules that shape this codebase:

1. **No dependencies.** Python standard library only, no frontend build. If a
   change needs a package, it probably needs a different design. The point is
   that `python3 server.py` works on a fresh box.
2. **No invented numbers.** Every value on a page comes off the machine. If
   something cannot be measured with the setup at hand, the dashboard says so
   rather than estimating. A plausible-looking number is worse than a dash.

Beyond that: the dashboard is read-only. No route runs a command, executes
arbitrary SQL, writes a file or controls a service.

## Before a pull request

Both suites need a running dashboard, so run them on the machine:

```bash
SRVMON_PASSWORD='...' python3 tools/selftest.py   # the whole API against real data
SRVMON_PASSWORD='...' node tools/uitest.js        # every page, in both languages
python3 tools/checktranslations.py                # no interface string left behind
```

`uitest.js` renders each page against the live API with a minimal DOM stub, so
it catches `undefined` fields that only show up in a browser. It fails if an
English page contains a string from the dictionary — that is a forgotten
`t()` wrapper.

New interface text goes through `t('…')` with the Russian original, and its
English translation into `web/i18n.js`. CI checks that nothing is missing.

## Releases

Pushing a tag builds one:

```bash
# bump VERSION in lib/__init__.py, add a section to CHANGELOG.md
git tag -a v1.2.3 -m "srvmon 1.2.3"
git push origin v1.2.3
```

CI verifies the tag matches the version in the code, runs the checks, packs an
archive without local state, takes the notes from the changelog and publishes
them with a checksum.
