# Mailcow + Cloudflare domain onboarding script

This repo includes an automation script to onboard a mail domain end-to-end:
`scripts/mailcow-add-domain.sh`.

## What it does

When run, the script performs the following steps:

1. **Create/ensure domain in Mailcow**
   - Adds the domain with default mailbox/alias quotas and policy values.
   - If the domain already exists, it continues without failing.

2. **Create/ensure mailbox**
   - Creates one mailbox using `--local` (default: `me`) with the selected
     display name and password.
   - If password is omitted, a random strong password is generated.
   - If mailbox already exists, it is left unchanged.

3. **Create catch-all alias**
   - Adds `@<domain> -> <local>@<domain>`.

4. **Generate DKIM key**
   - Requests/creates Mailcow DKIM key using selector `dkim` and 2048-bit size.

5. **Push DKIM TXT to Cloudflare**
   - Fetches the DKIM public record from Mailcow.
   - Removes any existing `dkim._domainkey.<domain>`/`_domainkey.<domain>` TXT
     records in Cloudflare for that zone.
   - Creates/updates `dkim._domainkey.<domain>` TXT record.

## Environment and credentials

The script reads the following values from the environment, or from
`~/.config/aether/env` (automatically sourced):

- `MAILCOW_API_KEY` (`X-API-Key` for Mailcow API)
- `MAILCOW_HOST` (defaults to `mail.sumeetsaini.com`)
- `CF_API_TOKEN` (Cloudflare API token)

It also requires:

- `jq`
- `curl`
- `openssl`

Example `~/.config/aether/env`:

```bash
MAILCOW_HOST="mail.sumeetsaini.com"
MAILCOW_API_KEY="..."
CF_API_TOKEN="..."
SERVER_IP4="49.12.43.116"
SERVER_IP6="2a01:4f8:c17:6484::1"
```

## Usage

```bash
scripts/mailcow-add-domain.sh <domain> [--local me] [--name "Display Name"] [--password STRING]
```

### Example (last run)

```bash
scripts/mailcow-add-domain.sh bangbangstudios.co.uk \
  --local jodh \
  --name "Jodh Saini" \
  --password "BangBang123!"
```

This created:

- domain: `bangbangstudios.co.uk`
- mailbox: `jodh@bangbangstudios.co.uk`
- catch-all: `@bangbangstudios.co.uk -> jodh@bangbangstudios.co.uk`
- DKIM TXT: `dkim._domainkey.bangbangstudios.co.uk`

## Notes

- Cloudflare zone must exist in the account bound to `CF_API_TOKEN`; otherwise the
  script will fail at the DNS step.
- The script does **not** create base web records (`A`, `AAAA`, etc.) or DMARC. Those
  must be managed separately (e.g. `scripts/cf-add-site.sh` for web+mail DNS).
- For deliverability, configure SPF/DKIM/DMARC together and run a test mail send
  after onboarding.
