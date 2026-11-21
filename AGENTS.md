# Agent instructions

Home Assistant integration that budgets the grid import per capacity-tariff window
and publishes the power a controllable load may draw. Read [`docs/design.md`](docs/design.md)
for the design and [`CONTEXT.md`](CONTEXT.md) for terms before changing code.

## Workflow

- **Strict TDD.** Write one failing test, run it and confirm it fails for the right reason,
  then write the minimum code to pass, then refactor. Never write production code without a
  failing test that requires it.
- Coverage must stay at 100% (line and branch); CI fails otherwise.
- Work in a worktree on a branch and open a pull request. Never edit the main checkout and
  never push to `main` (see Commands).
- Leave a pull request open for review: Claude reviews every one
  (`.github/workflows/claude-review.yml`, from `klconsultancy/workflows`) and a person
  merges it. Answer the review's comments before asking for the merge.
- `main` has a merge queue: a pull request is merged with **Merge when ready**, and the
  queue brings it up to date with `main` and runs the required checks again, one after the
  other. A workflow that gives a required check must also run on `merge_group`.
- PR titles and squash commits follow Conventional Commits (`feat:`, `fix:`, `docs:`,
  `test:`, `refactor:`, `build:`, `ci:`, `chore:`). They drive release-please.
- A release candidate is a tag `vX.Y.Z-rc.N` on `main`, pushed by an admin while
  release-please's release PR for `X.Y.Z` is open; the release workflow makes it a GitHub
  pre-release, which HACS offers as a beta. Any other rc tag fails the workflow and has to
  be deleted by hand. Every release gets
  `peak_basket.zip` (`script/release_zip.py`), which HACS installs (`hacs.json`): the
  integration with `manifest.json`'s version set to the tag's, because on `main` it still
  has the last release's. Releases are immutable once published, so the release workflow
  keeps each release and pre-release a draft until the zip is attached, then publishes it
  (`draft` and `force-tag-creation` in `release-please-config.json`). Never make one with
  `release-as` and `prerelease` in `release-please-config.json`: release-please would count
  the rc as the last release, and the final release's notes would leave out what the rc
  had. Before merging a release PR, check that its notes cover everything since the last
  stable release.
- When a design choice or term changes, update `docs/design.md` or `CONTEXT.md` in the same
  PR. Do not create ADRs.
- Dependency updates come from Renovate (`renovate.json`): grouped on the 1st and the 15th
  and automerged once CI passes, except majors and the pin on `klconsultancy/workflows` (its
  own pull request, for review). A new Python minor version is raised by hand.

## Commands

Once per clone, on the host:

```sh
git config worktree.useRelativePaths true
```

Start a change in a new worktree (on the host):

```sh
git fetch origin
git worktree add .worktrees/<branch> -b <branch> origin/main
```

Run the tools inside the devcontainer, from the worktree:

```sh
devcontainer exec --workspace-folder . uv run --directory .worktrees/<branch> pytest
```

| Command                | Purpose                     |
| ---------------------- | --------------------------- |
| `uv run pytest`        | tests + coverage gate       |
| `uv run ruff format`   | format                      |
| `uv run ruff check`    | lint                        |
| `uv run mypy`          | type-check (strict)         |
| `uv run prek run -a`   | all pre-commit hooks        |

hassfest and the HACS validation run in CI only (the hassfest image is amd64-only).

Commit inside the devcontainer so the hooks run, push from the host, and remove the worktree
after the merge with `git worktree remove .worktrees/<branch>`.

## The model

- `custom_components/peak_basket/model/` is pure Python: it never imports Home Assistant.
  Everything Home Assistant-specific (reading entities, units, staleness, the recorder)
  lives in `coordinator.py`.
- Region rules live in profiles (`model/profiles.py`), never in the engine.
- Decide on measurements, never on status texts. Missing or stale input gives the safe
  fallback, never an unlimited one.
- Internally: W, Wh and seconds.
- `tests/test_jinja_parity.py` pins the engine to the Jinja template it was ported from.
  The fixture stays the macro's output. A deliberate change to the decision is stated in
  that test as a divergence, with its reason and its pinned count, and the PR says why.

## Home Assistant

- Follow the [integration quality scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
  rules up to Silver.
- Every user-facing string goes through `strings.json` and the translations (English and
  Dutch).
- The domain `peak_basket` is part of every entity and config entry: it does not change.
