# Contributing

Thanks for your interest. This is a Home Assistant helper integration that budgets the grid
import per capacity-tariff window.

## Before you write code

Open an issue first for anything beyond a small fix, so we can agree on the approach before
you spend time on it. Read [CONTEXT.md](CONTEXT.md) for the terms and
[docs/design.md](docs/design.md) for the design. Report security issues privately as
described in [SECURITY.md](SECURITY.md).

A new region is welcome: open an issue with the grid operator's rules (window length, peak
rule, billed minimum, when windows count) and a link to where they are published.

## Development

The tools run in the repository's devcontainer, with [uv](https://docs.astral.sh/uv/), on a git worktree:

```sh
git config worktree.useRelativePaths true
git worktree add .worktrees/my-change -b my-change origin/main
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . uv run --directory .worktrees/my-change pytest
```

Changes are test-first, and coverage stays at 100% (line and branch). CI also runs `ruff format --check`, `ruff check`, `mypy` (strict), hassfest and the HACS validation; `uv run prek run -a` runs the hooks locally. Every user-facing string goes through `strings.json` and the English and Dutch translations. [AGENTS.md](AGENTS.md) describes the workflow in full.

The model in `custom_components/peak_basket/model/` is pure Python and never imports Home Assistant.

## Pull requests

- Keep a pull request to one change, with tests.
- CI (lint, type check, tests, hassfest, HACS, dependency review and CodeQL) must pass before merging.
- Pull requests are squash-merged, so the pull request title becomes the commit message on `main`. Write it as a [Conventional Commit](https://www.conventionalcommits.org/) with a short imperative description, such as `feat: add the Swedish profile`: release-please derives the next version and the changelog from these titles. Use `feat!:` or a `BREAKING CHANGE:` footer for a change that breaks existing entities or configuration.

By contributing you agree that your contribution is licensed under the [MIT license](LICENSE), and you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
