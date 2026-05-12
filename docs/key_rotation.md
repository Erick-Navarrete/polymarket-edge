# Key Rotation Procedure — Polymarket Edge

## When to Rotate Keys

- Immediately if any key is suspected of being compromised
- After any team member with key access leaves
- Every 90 days as routine hygiene
- After running untrusted or third-party bot code
- If `.env` is accidentally committed to version control

## Keys to Rotate

### 1. Polygon Wallet Private Key

This is the highest-value secret — it controls all funds.

1. Generate a new wallet: `openssl rand -hex 32` or use MetaMask
2. Transfer all USDC and conditional tokens from old wallet to new wallet
3. Update `POLYGON_WALLET_ADDRESS` and `POLYGON_WALLET_PRIVATE_KEY` in `.env`
4. Set token allowances on the new wallet (USDC + conditional tokens)
5. Verify the new wallet can place a small test order on Polymarket
6. Destroy the old key — there is no recovery

**Warning:** Polygon wallet keys cannot be "rotated" on Polymarket's side. You must create a new wallet and move funds.

### 2. Polymarket API Credentials (API Key/Secret/Passphrase)

1. Go to Polymarket settings and generate new API credentials
2. Update all three values in `.env`: `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_API_PASSPHRASE`
3. Revoke the old credentials in Polymarket settings
4. Test with a paper order before resuming live trading

### 3. OpenAI API Key

1. Go to platform.openai.com/api-keys
2. Create a new key, update `OPENAI_API_KEY` in `.env`
3. Delete the old key
4. Verify AI agent strategy still works

### 4. Exchange API Keys (Binance, Coinbase)

1. Create new API keys on each exchange
2. Update `.env` values
3. Delete old keys on each exchange
4. Restrict new keys to minimum required permissions (read-only for data, no withdrawal)

### 5. Database Password

1. `ALTER USER polymarket WITH PASSWORD 'new_password';`
2. Update `POSTGRES_PASSWORD` in `.env` and docker-compose.yml
3. Restart all services

### 6. Redis

Redis has no built-in auth by default in our setup. If exposed:
1. Set a password in redis.conf: `requirepass newpassword`
2. Update `REDIS_URL` in `.env`
3. Restart Redis

### 7. Grafana Admin Password

1. Update `GRAFANA_ADMIN_PASSWORD` in `.env`
2. Reset via Grafana UI or: `grafana-cli admin reset-admin-password newpass`

## Emergency Rotation Checklist

If a key is known-compromised:

1. **Stop the engine immediately** — kill the process or set `LIVE_MODE=false` and restart
2. **Transfer funds** from the compromised wallet to a fresh one
3. **Rotate API keys** on all services (Polymarket, exchanges, OpenAI)
4. **Check trade history** for unauthorized orders
5. **Audit `.env` access** — who has read access to this machine?
6. **Rotate database + Grafana passwords**
7. **Document the incident** in `docs/incidents/`

## Storage Security

- `.env` is the **single source of truth** for all secrets
- `.env` is git-ignored — verify it is never committed: `git ls-files .env`
- Never log secrets — structlog should never receive raw keys
- In production, consider using a secret manager (AWS Secrets Manager, HashiCorp Vault)
- Never share `.env` via Slack, email, or any unencrypted channel
