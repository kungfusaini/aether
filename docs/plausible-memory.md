# Plausible memory guard

The Plausible service explicitly sets `nofile` soft/hard limits to 65,536.
Without this, the host's inherited limit of 1,073,741,816 caused Erlang/OTP 27
to configure 134,217,727 ports and allocate 1.5 GiB for its port table, despite
only about 35 active ports. See [Erlang system limits](https://www.erlang.org/docs/27/system/system_limits.html)
and [the Erlang file-limit allocation explanation](https://www.rabbitmq.com/blog/2022/08/30/high-initial-memory-consumption-of-rabbitmq-nodes-on-centos-stream-9).

## Deployment and verification

- Validate the fully merged production Compose configuration. Compare the
  rendered Plausible environment against the live container without printing
  secrets; preserve live settings rather than silently accepting defaults.
- In particular, the existing deployment uses `PLAUSIBLE_DISABLE_REGISTRATION=invite_only`.
  Supply that value when recreating the service; do not change registration
  policy as part of a resource fix.
- Recreate only `plausible` with `--no-deps --no-build --pull never`; do not
  recreate PostgreSQL, ClickHouse or Mailcow. Back up the prior overlay first.
- Check `/proc/1/limits` inside the new container and use the release's `rpc`
  command to inspect `:erlang.system_info(:port_limit)` and the `:port_table`
  entry in `:erlang.system_info(:allocated_areas)`. Constrain the diagnostic
  shell's own `nofile` to 65,536 before starting a diagnostic Erlang VM.
- Verify `/api/health`, `/js/script.js` and `/login` through the local TLS
  gateway (using curl `--resolve`), as well as database readability and
  unchanged non-Plausible container IDs. External Cloudflare requests may
  return 403 from server-side clients, which is not an origin health check.
- Compare Docker memory and host available RAM before and after. Startup
  measurements alone are not proof of long-term stability; inspect kernel OOM
  events during subsequent normal operation as needed.

## Verified deployment, 4 November 2026

Port-table allocation fell from 1,610,612,736 to 786,432 bytes; runtime port
limit became 65,536. Plausible Docker memory fell from approximately 2.315 GiB
to 348 MiB, and host available memory increased from 409 to 2,378 MiB during
the immediate checks. The exact later working set depends on traffic.

Only Plausible's container was replaced; its image and complete environment
were unchanged. Both databases remained running and readable, the existing
site count was unchanged, and all three origin routes returned HTTP 200.
No new kernel OOM events were recorded during the short verification window.
This does not establish that all future host-wide OOM events are prevented.
