## What and why

<!-- What changes, and the problem it solves. Link the issue if there is one. -->

## How it was tested

<!-- Commands you ran, what you clicked through in the UI, screenshots for UI changes. -->

## Checklist

- [ ] Targets `dev` (never `main`)
- [ ] `./dev.sh validate` ends with `all good` (ruff, mypy, pytest, UI lint, typecheck, tests, build)
- [ ] Tests, fixtures and docs use invented names only (Harbor Lights; Kaito, Mina, Ren, Sora, Tomoe)
- [ ] New settings are documented in `config.example.yaml` and the docs
- [ ] A database change adds a migration in `fanscut/db.py`; a renamed or moved config key adds a step to `CONFIG_MIGRATIONS`
- [ ] Commit messages are short, imperative, and the subject stays under ~50 characters
- [ ] AI tools were used for this change: say which parts, and that you reviewed them
