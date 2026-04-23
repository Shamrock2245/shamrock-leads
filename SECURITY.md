# SECURITY.md — ShamrockLeads Security Policy

## Secrets Management
- Local dev: .env file (git-ignored)
- Docker: docker-compose.yml environment block referencing .env
- Never commit secrets to version control
- Never log secrets in console output or Slack

## PII Protection
- Never log full PII — mask phone numbers, redact addresses
- Slack alerts show county, bond amount, score — never full PII together
- MongoDB Atlas requires authenticated connections

## Scraping Ethics
1. Rate-limit all requests (min 1s delay)
2. Respect robots.txt
3. Rotate User-Agent strings
4. Public data only
5. Auto-disable after 5 consecutive failures
