# Contributing to Unified Solution Migration Analyzer

Thanks for your interest in improving the analyzer. This project is small and
maintained part-time, so the rules are deliberately light.

## Ground rules

- Be respectful — see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- Open an issue **before** large changes so we can agree on direction.
- Bug reports with a concrete reproduction (workspace shape, redacted JSON
  excerpt, error message) are gold.
- Security issues: see [SECURITY.md](SECURITY.md) — **do not** file them as
  public issues.

## Development setup

```powershell
git clone https://github.com/anbergst_microsoft/USMA.git
cd USMA
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Quick sanity check:

```powershell
python -m pytest -q --basetemp=.pytest-tmp
sma doctor
```

## Pull request checklist

Before opening a PR, please confirm:

- [ ] Tests pass locally (`python -m pytest -q --basetemp=.pytest-tmp`).
- [ ] Lint is clean (`ruff check .`).
- [ ] Type-check is clean for changed files (`mypy src/usma/...`).
- [ ] New behaviour has at least one unit test under `tests/`.
- [ ] User-visible changes touch `README.md` and/or `QUICKSTART.md`.
- [ ] Commit messages follow the repo's `type(scope): summary` style
  (e.g. `feat(storage): ...`, `fix(pipelines): ...`, `docs: ...`).

## Module conventions

Each module under `src/usma/modules/<name>/` exposes:

- `analyzer.py` with a class providing `.run() -> <ModulePydanticModel>`
- `models.py` with Pydantic v2 models (no business logic)
- `arm_client.py` and/or `sql_client.py` for I/O, isolated for mocking
- `reporting.py` writing JSON / CSV / Markdown
- `html_report.py` writing the module's HTML report

When adding a new module, also:

1. Wire it into `src/usma/cli.py` (own `analyze-...`
   command + the appropriate `[N/M]` step in `analyze-all`).
2. Add an entry to `_MODULES` in
   `src/usma/reporting/index_report.py`.
3. Update the missing-pill count assertion in
   `tests/test_module_html_reports.py::test_index_lists_available_and_missing`.
4. Add tests under `tests/`.
5. Update `README.md` and `QUICKSTART.md` (modules table + new section).

## Reporting bugs

Please include:

- Output of `sma doctor`.
- The CLI command you ran.
- Tail of `sma.log` (with secrets redacted).
- Python and OS version.

## License of contributions

By submitting a contribution you agree that it will be licensed under the
project's [MIT License](LICENSE).
