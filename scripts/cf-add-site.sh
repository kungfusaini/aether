#!/usr/bin/env bash
# Add a new site (web + mail) to Cloudflare DNS, matching the pattern
# used by reliq.digital and the other sites in this account.
#
# Usage:
#   cf-add-site.sh <domain> [--no-mail] [--no-proxy] [--ip4 X] [--ip6 Y]
#
# Defaults:
#   ip4 = 49.12.43.116
#   ip6 = 2a01:4f8:c17:6484::1
#
# Creates (web):
#   A     @     -> ip4   (proxied)
#   A     www   -> ip4   (proxied)
#   AAAA  @     -> ip6   (proxied)
#   AAAA  www   -> ip6   (proxied)
#
# Creates (mail, unless --no-mail):
#   A     mail          -> ip4 (DNS-only; needed for SMTP)
#   MX    @             -> mail.<domain>   (priority 10)
#   CNAME autoconfig    -> mail.<domain>   (proxied)
#   CNAME autodiscover  -> mail.<domain>   (proxied)
#   TXT   @  SPF        -> "v=spf1 ip4:<ip4> ip6:<ip6>/64 a mx ~all"
#
# DKIM is NOT created here — grab the record from the Mailcow admin UI after
# enabling the domain there, and add the TXT manually (or re-run with --dkim).
#
# Requires: jq, curl, and CF_API_TOKEN — set in the env or in ~/.config/aether/env

set -euo pipefail

# Load shared secrets / defaults.
if [[ -r "$HOME/.config/aether/env" ]]; then
    set -a; . "$HOME/.config/aether/env"; set +a
fi

DOMAIN=""
WITH_MAIL=1
PROXIED=true
IP4="${SERVER_IP4:-49.12.43.116}"
IP6="${SERVER_IP6:-2a01:4f8:c17:6484::1}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-mail)  WITH_MAIL=0; shift ;;
        --no-proxy) PROXIED=false; shift ;;
        --ip4)      IP4="$2"; shift 2 ;;
        --ip6)      IP6="$2"; shift 2 ;;
        -h|--help)  sed -n '2,28p' "$0"; exit 0 ;;
        -*)         echo "unknown flag: $1" >&2; exit 1 ;;
        *)          DOMAIN="$1"; shift ;;
    esac
done

if [[ -z "$DOMAIN" ]]; then
    sed -n '2,5p' "$0" >&2
    exit 1
fi

TOKEN="${CF_API_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
    echo "error: CF_API_TOKEN not set (export it, or put it in ~/.config/aether/env)" >&2
    exit 1
fi

API="https://api.cloudflare.com/client/v4"
AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

cf() { curl -fsS "${AUTH[@]}" "$@"; }

ZONE_ID="$(cf "$API/zones?name=$DOMAIN" | jq -r '.result[0].id // empty')"
if [[ -z "$ZONE_ID" ]]; then
    echo "error: zone '$DOMAIN' not found in this Cloudflare account" >&2
    exit 1
fi
echo "zone: $DOMAIN ($ZONE_ID)"

# upsert <payload> [matcher_jq]
# Creates the record. If one with the same name+type (and optional matcher)
# already exists, deletes it first so we end up with exactly one.
upsert() {
    local payload="$1" matcher="${2:-true}"
    local name type
    name="$(echo "$payload" | jq -r .name)"
    type="$(echo "$payload" | jq -r .type)"

    local existing_ids
    existing_ids="$(cf "$API/zones/$ZONE_ID/dns_records?type=$type&name=$name&per_page=100" \
        | jq -r ".result[] | select($matcher) | .id")"
    for id in $existing_ids; do
        cf -X DELETE "$API/zones/$ZONE_ID/dns_records/$id" >/dev/null
        echo "  - removed existing $type $name ($id)"
    done

    local resp
    resp="$(cf -X POST "$API/zones/$ZONE_ID/dns_records" --data "$payload")"
    if [[ "$(echo "$resp" | jq -r .success)" == "true" ]]; then
        echo "  + $type $name"
    else
        echo "  ! $type $name: $(echo "$resp" | jq -c .errors)" >&2
    fi
}

a()    { jq -nc --arg n "$1" --arg ip "$2" --argjson p "$3" \
            '{type:"A",name:$n,content:$ip,ttl:1,proxied:$p}'; }
aaaa() { jq -nc --arg n "$1" --arg ip "$2" --argjson p "$3" \
            '{type:"AAAA",name:$n,content:$ip,ttl:1,proxied:$p}'; }
mx()   { jq -nc --arg n "$1" --arg c "$2" \
            '{type:"MX",name:$n,content:$c,priority:10,ttl:1,proxied:false}'; }
cname(){ jq -nc --arg n "$1" --arg c "$2" --argjson p "$3" \
            '{type:"CNAME",name:$n,content:$c,ttl:1,proxied:$p}'; }
txt()  { jq -nc --arg n "$1" --arg c "$2" \
            '{type:"TXT",name:$n,content:$c,ttl:1,proxied:false}'; }

echo "web records:"
upsert "$(a    "$DOMAIN"      "$IP4" $PROXIED)"
upsert "$(a    "www.$DOMAIN"  "$IP4" $PROXIED)"
upsert "$(aaaa "$DOMAIN"      "$IP6" $PROXIED)"
upsert "$(aaaa "www.$DOMAIN"  "$IP6" $PROXIED)"

if [[ $WITH_MAIL -eq 1 ]]; then
    echo "mail records:"
    upsert "$(a     "mail.$DOMAIN"         "$IP4" false)"
    upsert "$(mx    "$DOMAIN"              "mail.$DOMAIN")"
    upsert "$(cname "autoconfig.$DOMAIN"   "mail.$DOMAIN" true)"
    upsert "$(cname "autodiscover.$DOMAIN" "mail.$DOMAIN" true)"
    # SPF: there should be exactly one v=spf1 TXT on the apex.
    upsert "$(txt   "$DOMAIN" "v=spf1 ip4:$IP4 ip6:${IP6%::*}::/64 a mx ~all")" \
           '.content | startswith("\"v=spf1") or startswith("v=spf1")'
fi

echo "done. (DKIM still needs to be added from Mailcow admin after enabling the domain.)"
