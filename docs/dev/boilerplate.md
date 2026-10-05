# BulliExplorer — Project Boilerplate

> Adapted from the general Python project boilerplate for this specific
> project: a solo-authored FastAPI + Jinja2 + HTMX blog with PostGIS-backed
> campsites/routes. See `bulliexplorer-tech-concept.md` for architecture and
> hosting; this doc covers local project setup and dev workflow.
> Target: macOS + `uv` + Python 3.13.

**Scope note:** the source boilerplate this is adapted from targets
CRA/BSI-regulated commercial software (SBOM mandates, license gates, managed
SAST rulesets, audit-evidence coverage gates). None of that applies to a
personal blog with one user and no market placement. This version keeps
everything that's good engineering practice regardless of project size, and
explicitly drops or defers the compliance layer — each cut is noted with why,
so it's a decision, not an oversight, if this project ever grows beyond "solo
hobby."

---

## 0. Principles

- **One tool for the toolchain**: `uv` replaces `pyenv`, `virtualenv`, `pip`,
  `pip-tools`, `pipx`. No exceptions.
- **`uv sync` is the only setup command** after cloning. Everything derives
  from `pyproject.toml` + `uv.lock`.
- **`.venv` lives in the project root** — discovered automatically by uv,
  VS Code, pyright.
- **Shift left**: pre-commit hooks catch what CI would catch, minutes earlier.
- **Centralized logging from day one** — one log factory, no scattered
  `logging.basicConfig` calls.
- **Framework-free `services/`** — business logic doesn't import FastAPI,
  Jinja2, or HTMX. Templates and routes are a thin layer on top.

---

## 1. uv — bootstrap

```bash
# one-time machine setup
brew install uv
uv python install 3.13

# project (already scaffolded per the tech concept doc — repeated here for reference)
uv init bulliexplorer --app
cd bulliexplorer
uv python pin 3.13            # writes .python-version — committed
```

## 2. uv — virtual environment

```bash
uv sync                       # reads .python-version, creates .venv/, installs everything
```

- No activation needed: `uv run <cmd>` runs inside `.venv` automatically.
- `.venv/` is always gitignored — disposable, `rm -rf .venv && uv sync` rebuilds identically.

## 3. uv — dependencies

```bash
# core (matches the tech concept)
uv add fastapi "uvicorn[standard]" jinja2 python-multipart
uv add sqlalchemy geoalchemy2 "psycopg[binary]" alembic
uv add sqladmin "fastapi-users[sqlalchemy]"
uv add markdown-it-py pydantic pydantic-settings boto3 python-dotenv

# dev group
uv add --dev ruff pyright pytest pytest-cov pytest-asyncio httpx

# security group (trimmed — see §7)
uv add --dev --group security bandit detect-secrets pip-audit pre-commit
```

Lock rule: **`uv.lock` committed** (this is an application, not a published
library) — reproducible builds on the Hetzner box.

---

## 4. Project structure

```
bulliexplorer/
├── .python-version
├── .venv/                        # gitignored
├── pyproject.toml
├── uv.lock                       # committed
├── Makefile
├── .pre-commit-config.yaml
├── .env.example                  # committed; .env is not
├── .secrets.baseline             # detect-secrets — committed
├── app/
│   ├── __init__.py
│   ├── main.py                   # create_app() factory + lifespan
│   ├── core/
│   │   └── config.py             # pydantic-settings
│   ├── models/                   # SQLAlchemy + GeoAlchemy2 models
│   │   ├── campsite.py           # PointField
│   │   └── route.py              # LineStringField (GPX tracks)
│   ├── services/                 # business logic — no FastAPI/Jinja2 imports
│   ├── routes/                   # FastAPI routers, one per resource
│   ├── admin.py                  # SQLAdmin ModelViews + fastapi-users auth
│   └── utils/
│       └── log_factory.py
├── templates/                    # Jinja2 — server-rendered HTML
│   ├── base.html
│   └── partials/                 # HTMX fragment responses
├── static/
│   ├── htmx.min.js
│   ├── alpine.min.js
│   ├── maplibre-gl.js / .css
│   └── style.css
├── content/
│   └── posts/                    # Markdown blog posts, frontmatter validated by Pydantic
├── tests/
│   ├── conftest.py
│   ├── unit/
│   └── integration/
├── alembic/                      # migrations
└── docker-compose.yml            # local dev: Postgres/PostGIS
```

- `Path(__file__)`-anchored paths everywhere — no relative-path fragility.
- `services/` stays framework-free — this is what keeps geo/business logic
  testable without spinning up FastAPI.
- **Dropped from the general boilerplate:** `docs/` + Zensical site. A
  README and inline comments are enough for a one-person project — a docs
  site is overhead with no reader other than future-you. Revisit only if
  this gets contributors.

---

## 5. `pyproject.toml` — reference configuration

```toml
[project]
name = "bulliexplorer"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "jinja2>=3.1.0",
    "python-multipart>=0.0.12",
    "sqlalchemy>=2.0.0",
    "geoalchemy2>=0.15.0",
    "psycopg[binary]>=3.2.0",
    "alembic>=1.14.0",
    "sqladmin>=0.20.0",
    "fastapi-users[sqlalchemy]>=14.0.0",
    "markdown-it-py>=3.0.0",
    "pydantic-settings>=2.7.0",
    "boto3>=1.35.0",
    "python-dotenv>=1.0.0",
]

[dependency-groups]
dev = [
    "ruff>=0.9.0",
    "pyright>=1.1.400",
    "pytest>=8.4.1",
    "pytest-cov>=7.1.0",
    "pytest-asyncio>=0.25.0",
    "httpx>=0.28.0",
]
security = [
    "bandit>=1.8.6",
    "detect-secrets",
    "pip-audit",
    "pre-commit",
]

# ── Lint / format ─────────────────────────────────────────────────────────
[tool.ruff]
line-length = 120
target-version = "py313"
exclude = ["alembic/versions", ".venv"]

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "C4", "UP", "S"]
ignore = ["PLR2004"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]

[tool.ruff.format]
quote-style = "double"

# ── Types ─────────────────────────────────────────────────────────────────
[tool.pyright]
pythonVersion = "3.13"
typeCheckingMode = "basic"
include = ["app"]
exclude = ["tests", "alembic"]

# ── SAST ──────────────────────────────────────────────────────────────────
[tool.bandit]
exclude_dirs = ["tests"]
skips = ["B101"]

# ── Tests + coverage ────────────────────────────────────────────────────────
[tool.pytest.ini_options]
minversion = "7.0"
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = [
    "-ra",
    "--strict-markers",
    "--tb=short",
    "--cov=app",
    "--cov-report=term-missing",
    "--cov-fail-under=60",        # floor, not a target — raise as the project matures
]
markers = [
    "unit: no external dependencies",
    "integration: database or external services",
]

[tool.coverage.run]
source = ["app"]
omit = ["*/alembic/*", "*/__init__.py"]
```

**Dropped from the general boilerplate, and why:**

| Dropped | Reason |
|---|---|
| `semgrep` | Needs a managed ruleset/token to be worth much beyond what `bandit`+`ruff` already cover; adds CI time for a one-person repo |
| `safety` | Second CVE database on top of `pip-audit` — redundant for a solo project; GitHub Dependabot already flags known-vulnerable deps automatically (already in the security baseline) |
| `cyclonedx-bom` (SBOM) | CRA-mandatory for commercial software placed on the market — doesn't apply to a personal blog |
| `pip-licenses` gate | License-compliance auditing is for products with legal exposure, not a hobby project's own dependency tree |
| `radon` complexity gates | "Secure by design" audit evidence for regulated codebases — not useful signal at this scale |
| `gitleaks` full-history scan | Worth running once before ever making the repo public, not baseline CI |
| `--cov-fail-under=80` → `60` | 80% is an audit-evidence number, not a quality signal. A floor that blocks you from committing early-stage code is worse than no floor — raise it once the app has real shape |

---

## 6. Config & logging

Same pattern as the general boilerplate, unchanged — it's good practice
regardless of project size:

- One `Settings` class in `app/core/config.py`, `pydantic-settings`, lazy
  `@lru_cache` singleton via `get_settings()`.
- `APP_ENV` → `is_development` / `is_production` properties.
- `app/utils/log_factory.py`: `get_logger(__name__)` everywhere,
  `configure_logging()` called once at startup, JSON output toggle
  (`LOG_JSON=true` on Hetzner, human-readable locally).

**Simplified vs. the general boilerplate:** skip the `ContextVar`-based
trace-ID correlation middleware for v1. It's genuinely useful once you have
concurrent requests worth correlating across services — a single FastAPI
process serving a personal blog doesn't need it yet. Add it if/when you
split out a background worker or a second service.

---

## 7. Security toolchain (trimmed)

| Tool | Purpose |
|---|---|
| `ruff` | Lint + format |
| `bandit` | Python-specific SAST |
| `pyright` | Static types |
| `detect-secrets` | Pre-commit secrets baseline |
| `pip-audit` | CVE scan against OSV/PyPI advisories |
| `pytest-cov` | Coverage floor (60%, see §5) |
| `pre-commit` | Runs all of the above before commit reaches CI |

This is the same day-1 security baseline already decided for the project
(2FA on admin, SSH hardening, ufw, R2 backups, Dependabot, Sentry,
UptimeRobot, Cloudflare) — see the tech concept doc — with the
*code-level* half added here.

### `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.9.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: [--baseline, .secrets.baseline]
  - repo: https://github.com/PyCQA/bandit
    rev: 1.8.6
    hooks:
      - id: bandit
        args: [-c, pyproject.toml]
```

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

---

## 8. Testing

- `tests/unit/` (services, no DB) + `tests/integration/` (needs
  Postgres/PostGIS — use the `docker-compose.yml` local DB).
- Config tests: canonical env key, alias, missing key — same minimum bar as
  the general boilerplate.
- Geo-specific: at least one integration test that round-trips a
  `LineStringField` (GPX track) through PostGIS and one that queries
  campsites within a radius — this is the part of the stack most likely to
  break silently.

---

## 9. Web-service specifics (this project *is* the web service)

Unlike the general boilerplate where this is an optional addendum,
BulliExplorer is a web service from the start, so this is just part of the
baseline:

- **FastAPI factory pattern**: `create_app()` in `app/main.py` + lifespan
  context manager for DB engine startup/shutdown.
- **Security-headers middleware**: `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, CSP (allow MapLibre's tile source
  domain), Permissions-Policy — matches the "security headers/HSTS" item
  already in the concept doc's day-1 baseline.
- **Health endpoint**: `/health` — used by UptimeRobot (already planned)
  and Docker's `HEALTHCHECK`.
- **SQLAdmin mounted at `/admin`**, gated behind `fastapi-users` auth +
  2FA — matches the concept doc's admin security requirement.
- **Docker**: multi-stage build — `uv sync --frozen --no-dev` in the
  builder stage, copy `.venv` into a slim runtime image, non-root user,
  `HEALTHCHECK` hitting `/health`. Full deploy flow is in the tech concept
  doc's Hetzner section.

- **Jinja templates trigger JS/TS-parser false positives, by design, not
  by bug**: any `{{ variable | tojson }}` inside a `<script>` block (route/
  amenity GeoJSON payloads, JSON-LD structured data) reads as invalid
  JavaScript to a JS/TS-aware static analyzer, since it's not valid JS
  until Jinja actually renders it. This will recur on every such block,
  in every session — `djlint templates/ --check` is the authoritative
  linter for this file type (now a required `ci.yml` step, not just a
  local `make` target), and it passing is the only signal that matters.
  Don't re-diagnose this from scratch each time it's flagged.

**Explicitly skipped for v1:** trace-ID middleware (§6), OpenTelemetry.
DAST (OWASP ZAP baseline scanning) was later added — see
`docs/dev/security_review_owasp.md` Phase 6 (`zap-baseline.yml`,
manually-triggered, scoped to the public homepage only). Reasonable
additions still deferred: none earn their setup cost yet — same
"defer until traffic/complexity justifies it" call already made for
observability in the concept doc.

---

## 10. Makefile

```makefile
SRC := app tests

.PHONY: build-env
build-env: ## Create .venv + install all dependencies
	uv sync

.PHONY: format
format: ## Format code (mutating)
	uv run ruff format $(SRC)
	uv run ruff check $(SRC) --fix

.PHONY: lint
lint: ## Lint + types (report only)
	uv run ruff format $(SRC) --check
	uv run ruff check $(SRC)
	uv run pyright $(SRC)

.PHONY: test
test: ## Run tests (coverage floor via addopts)
	uv run pytest

.PHONY: security
security: ## bandit + detect-secrets + pip-audit
	uv run bandit -r app -c pyproject.toml
	uv run detect-secrets scan app/ | diff .secrets.baseline -
	uv run pip-audit

.PHONY: dev
dev: ## Run the app locally with reload
	uv run uvicorn app.main:app --reload

.PHONY: db-upgrade
db-upgrade: ## Apply Alembic migrations
	uv run alembic upgrade head

.PHONY: db-revision
db-revision: ## Autogenerate a new migration — usage: make db-revision m="add campsite table"
	uv run alembic revision --autogenerate -m "$(m)"

.PHONY: clean
clean: ## Remove caches and build artefacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name htmlcov -exec rm -rf {} +

.PHONY: ci
ci: lint test security ## Full CI pipeline
```

**Dropped from the general boilerplate:** `sbom`, `licenses`, `complexity`,
`docs-serve`, `docs-build` targets — all tied to the compliance/docs-site
layer cut in §5 and §4.

---

## 11. `.gitignore`

```gitignore
# Python
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
htmlcov/
.coverage

# Environment
.env
.env.*
!.env.example

# Logs
logs/

# Media (large, lives in R2 not git)
static/uploads/

# Build
dist/
build/
*.egg-info/

# macOS
.DS_Store
```

---

## 12. New-project checklist

```
[ ] uv init --app + uv python pin 3.13
[ ] uv sync → .venv/ created
[ ] pyproject.toml: dependency groups + tool config (§5)
[ ] app/ skeleton: config.py, log_factory.py, models/, services/, routes/, admin.py
[ ] templates/ + static/ (htmx, alpine, maplibre) scaffolded
[ ] content/posts/ with one test Markdown post
[ ] tests/: config tests + one geo round-trip test; coverage floor at 60%
[ ] .gitignore + .env.example + README
[ ] pre-commit install; detect-secrets baseline
[ ] Makefile with the target surface above
[ ] docker-compose.yml for local Postgres/PostGIS
[ ] Alembic initialized, first migration (campsites + routes tables)
```