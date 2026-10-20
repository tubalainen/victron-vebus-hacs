# Development

[← Back to the README](../README.md) · [Documentation index](README.md)

```bash
pip install -r requirements_test.txt -r requirements_lint.txt
pytest --cov
ruff check . && ruff format --check . && mypy custom_components/victron_vebus
```

How the code is organised:

- **`custom_components/victron_vebus/vebus/`:** the protocol package. It has no Home
  Assistant imports and is type checked in strict mode.
- **`tests/vebus/simulator.py`:** a scripted MK3 with a MultiPlus-II behind it. Both the
  protocol tests and the Home Assistant tests use it.

## Releasing

Bump `version` in `custom_components/victron_vebus/manifest.json` (e.g. `0.2.0`) in a
pull request and merge it to `main`. That's all: the release workflow sees the new
version, creates the `v0.2.0` tag on the merge commit, publishes the GitHub release with
generated notes and attaches `victron_vebus.zip`. HACS then offers the new version as
an update. Versions with a suffix such as `0.2.0-beta1` become pre-releases. To give a
release its own text, add `docs/releases/vX.Y.Z.md` in the same pull request; the
generated notes are added below it.

Pushing a `vX.Y.Z` tag by hand, or publishing a release in the GitHub UI, also works.
The workflow then only checks that the tag matches the manifest version and attaches
the zip. A failed release can be retried from the Actions tab (*Release → Run
workflow*).
