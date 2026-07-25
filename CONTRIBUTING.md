# Contributing

Thanks for helping improve `codex-skill-toggle`.

## Before opening a pull request

Make one focused change, add or update a regression test, and run:

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile src/skill_toggle.py
```

Please keep private Codex settings, credentials, receipts, disabled vendor files, and personal absolute paths out of commits.

## Pull requests

Explain what changed, why it changed, how it was tested, and how the change can be rolled back. GitHub Actions must pass before merging to `main`.
