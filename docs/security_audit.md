# Security Audit — Dependency Tree (2026-05-12)

## Methodology
- `pip-audit` for known CVEs in installed packages
- Manual PyPI verification for each dependency (author, publisher, trusted publishing)
- Typosquatting analysis against high-value targets

## Results

### Vulnerable Packages (pip-audit)
| Package | Version | CVE | Fix |
|---------|---------|-----|-----|
| pip | 26.0.1 | CVE-2026-3219, CVE-2026-6357 | Upgrade to pip >= 26.1 |

No vulnerabilities found in project dependencies themselves.

### Verified Legitimate (Major ecosystem packages, no concerns)
- pydantic, pydantic-settings, httpx, websockets, redis, psycopg, sqlalchemy,
  numpy, python-dotenv, structlog, tenacity, fastapi, uvicorn, pytest,
  pytest-asyncio, ruff, mypy, openai, chromadb, sentence-transformers

### Verified — Official (Niche but confirmed publisher)
- **py-clob-client** (v0.34.6) — Official Polymarket CLOB client.
  Published via Trusted Publishing from `Polymarket/py-clob-client` repo.
  Author: Polymarket Engineering. MIT license. **No concerns.**

### Flagged — Unofficial with Security Concerns
- **pmxt** (v2.40.6) — Third-party "unified prediction market API" (CCXT equivalent).
  **Red flags:**
  1. **Unofficial** — not published by Polymarket or Kalshi
  2. **Requires Node.js sidecar** (`pmxtjs` npm package) — private keys flow through this
  3. **Not Trusted Publishing** — uploaded with standard credentials
  4. **Extreme release cadence** — 120+ versions in 4 months
  5. Maintainer: RealFishSam, repo: github.com/pmxt-dev/pmxt

  **Recommendation:** Remove pmxt dependency. Use py-clob-client (official) for
  Polymarket and write a thin Kalshi adapter if needed. Do NOT pass API keys or
  private keys through the pmxtjs sidecar.

### Dev-Protocol Warning (from CLAUDE.md)
The `dev-protocol` GitHub org was hijacked in Feb 2026 and pushes key-stealing malware.
None of our dependencies reference this org.

## Actions Taken
1. Flagged pmxt for removal (security concern)
2. All other dependencies verified safe
3. pip should be upgraded to >= 26.1 to resolve CVE
