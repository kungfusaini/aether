#!/bin/bash
# Root-owned forced command for the dedicated babbi.world SSH key.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077

# No shell, arbitrary command, image name, path, or Compose service is accepted.
command_pattern='^deploy ([0-9a-f]{40}) ([A-Za-z0-9][A-Za-z0-9-]{0,38}(\[bot\])?)$'
if [[ ! ${SSH_ORIGINAL_COMMAND:-} =~ $command_pattern ]]; then
    echo 'Only deploy <commit-sha> <github-actor> is permitted.' >&2
    exit 64
fi
revision=${BASH_REMATCH[1]}
actor=${BASH_REMATCH[2]}
repository=ghcr.io/ashbreaksthecode/babbi-world
candidate="$repository:$revision"

# The only credential is the current Actions run's short-lived GITHUB_TOKEN.
# Treat tokens as opaque (installation tokens may contain JWT punctuation).
# Bound only transport size/time and reject whitespace; never echo or use argv.
if ! IFS= read -r -t 15 -n 16384 token || [[ -z $token || ${#token} -ge 16384 || $token =~ [[:space:][:cntrl:]] ]]; then
    echo 'A single-line GitHub Actions token is required on stdin.' >&2
    exit 64
fi

exec 9>/run/lock/babbi-world-deploy.lock
flock -w 300 9
registry_config=$(mktemp -d /run/babbi-world-registry.XXXXXX)
trap 'rm -rf -- "$registry_config"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export DOCKER_CONFIG="$registry_config"
printf '%s' "$token" | timeout 30 docker login ghcr.io -u "$actor" --password-stdin >/dev/null
unset token
# Do not persist credentials in /root/.docker or change other services' login.
timeout 180 docker pull "$candidate"

actual_revision=$(docker image inspect "$candidate" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
source=$(docker image inspect "$candidate" --format '{{index .Config.Labels "org.opencontainers.image.source"}}')
image=$(docker image inspect "$candidate" --format '{{index .RepoDigests 0}}')
if [[ $actual_revision != "$revision" || ${source,,} != https://github.com/ashbreaksthecode/babbi.world || ! $image =~ ^ghcr\.io/ashbreaksthecode/babbi-world@sha256:[0-9a-f]{64}$ ]]; then
    echo 'Image identity did not match the requested repository and commit.' >&2
    exit 65
fi

cd /var/www/containers
compose=(docker compose -f docker-compose.yml -f docker-compose-prod.yml -f docker-compose-mailcow.yml -f docker-compose-plausible.yml)
container=$("${compose[@]}" ps --all -q babbi_world)
previous=
if [[ -n $container ]]; then
    previous=$(docker inspect "$container" --format '{{.Image}}')
    # Retain the exact old local image, including when the site is stopped.
    docker tag "$previous" "$repository:rollback"
fi

rollback() {
    if [[ -z $previous ]]; then
        echo 'No previous image exists; Aether operator attention required.' >&2
        return
    fi
    echo 'Deploy failed; restoring the previous babbi.world image.' >&2
    if timeout 40 env BABBI_WORLD_IMAGE="$previous" "${compose[@]}" up -d --no-deps --pull never --scale babbi_world=1 babbi_world; then
        timeout 10 docker exec gateway_nginx nginx -t && timeout 10 docker exec gateway_nginx nginx -s reload
    else
        echo 'Rollback failed; Aether operator attention required.' >&2
    fi
}

replacement_started=false
deployment_succeeded=false
finish() {
    status=$?
    trap - EXIT INT TERM
    # Includes unexpected errors and timeout signals after replacement started.
    if [[ $replacement_started == true && $deployment_succeeded != true ]]; then
        rollback || echo 'Rollback or gateway reload failed; operator attention required.' >&2
    fi
    rm -rf -- "$registry_config"
    exit "$status"
}
trap finish EXIT
replacement_started=true
if ! BABBI_WORLD_IMAGE="$image" "${compose[@]}" up -d --no-deps --pull never --scale babbi_world=1 babbi_world; then
    exit 1
fi
container=$("${compose[@]}" ps -q babbi_world)
ready=false
for attempt in {1..20}; do
    if docker exec "$container" wget -q -T 3 -O /dev/null http://127.0.0.1/; then
        ready=true
        break
    fi
    sleep 1
done
if [[ $ready != true ]] || ! docker exec gateway_nginx nginx -t || ! docker exec gateway_nginx nginx -s reload; then
    exit 1
fi
if ! curl --fail --silent --show-error --retry 5 --retry-all-errors --retry-delay 1 --max-time 10 \
    --resolve babbi.world:443:127.0.0.1 https://babbi.world/ -o /dev/null; then
    exit 1
fi
running_revision=$(docker inspect "$container" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
if [[ $running_revision != "$revision" ]]; then
    exit 1
fi
deployment_succeeded=true
printf 'Deployed babbi.world commit %s (%s).\n' "$revision" "$image"
