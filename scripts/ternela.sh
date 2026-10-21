#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
umask 077

fail() { printf '%s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: sh ternela.sh install|start|stop|status|logs|update|updates|backup|restore|remove [options]
  --dir PATH          One managed folder (default: ~/ternela)
  --port NUMBER       Install on this loopback port (default: 8765)
  --auto-update       Explicitly opt in during install
  --no-auto-update    Explicitly opt out during install
  updates on|off      Change consent on an existing running install
  backup              Save a compressed snapshot in this folder/backups
  restore --file PATH Restore a snapshot; first back up current data
  remove --purge      Also delete live volume, folder, backups and local image
Docker Desktop/Engine with Compose is required. No host Python or sudo is used.
EOF
}

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
install_dir="$HOME/ternela"
if [ -f "$script_dir/.ternela-install" ]; then install_dir=$script_dir; fi
command_name=${1:-help}
if [ "$#" -gt 0 ]; then shift; fi
port=8765; consent=ask; purge=false; choice=''; archive_file=''
while [ "$#" -gt 0 ]; do
    case $1 in
        --dir) [ "$#" -ge 2 ] || fail '--dir needs a path'; install_dir=$2; shift 2 ;;
        --port) [ "$#" -ge 2 ] || fail '--port needs a number'; port=$2; shift 2 ;;
        --auto-update) [ "$consent" = ask ] || fail 'Choose one update preference'; consent=true; shift ;;
        --no-auto-update) [ "$consent" = ask ] || fail 'Choose one update preference'; consent=false; shift ;;
        --file) [ "$#" -ge 2 ] || fail '--file needs a backup path'; archive_file=$2; shift 2 ;;
        --purge) purge=true; shift ;;
        on|off) [ -z "$choice" ] || fail 'Choose on or off'; choice=$1; shift ;;
        *) fail "Unknown option: $1" ;;
    esac
done
case $command_name in help|--help|-h) usage; exit 0 ;; install|start|stop|status|logs|update|updates|backup|restore|remove) ;; *) usage; exit 1 ;; esac
case $install_dir in /*) ;; *) install_dir="$PWD/$install_dir" ;; esac
while [ "$install_dir" != / ] && [ "${install_dir%/}" != "$install_dir" ]; do install_dir=${install_dir%/}; done
[ "$install_dir" != / ] && [ "$install_dir" != "$HOME" ] || fail 'Choose a dedicated install folder'
[ ! -L "$install_dir" ] || fail 'The install folder must not be a symbolic link'
command -v docker >/dev/null 2>&1 || fail 'Install Docker Desktop (or Docker Engine + Compose), then retry'
# A managed install may pin a context without changing another project's default.
docker_context=''
if [ -e "$install_dir/.ternela-context" ] || [ -L "$install_dir/.ternela-context" ]; then
    [ -f "$install_dir/.ternela-context" ] && [ ! -L "$install_dir/.ternela-context" ] || fail 'Invalid Docker context marker'
    docker_context=$(cat "$install_dir/.ternela-context")
    [ -n "$docker_context" ] || fail 'Empty Docker context marker'
elif [ "$command_name" = install ]; then
    docker_context=$(command docker context show)
    [ -n "$docker_context" ] || fail 'Select a named Docker context before installing'
fi
docker_cli() {
    if [ -n "$docker_context" ]; then command docker --context "$docker_context" "$@";
    else command docker "$@"; fi
}
engine_id=$(docker_cli info --format '{{.ID}}' 2>/dev/null) || fail 'Start Docker Desktop/Engine, then retry'
[ -n "$engine_id" ] || fail 'Docker engine identity is unavailable'
docker_cli compose version >/dev/null 2>&1 || fail 'Docker Compose v2 is required'

if [ "$command_name" = install ]; then
    case $port in ''|*[!0-9]*) fail 'Port must be a number' ;; esac
    [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || fail 'Use a port between 1024 and 65535'
    [ ! -e "$install_dir" ] || fail 'Folder already exists; use start, or choose another --dir'
    source_dir=$(CDPATH='' cd -- "$script_dir/.." && pwd -P)
    [ -f "$source_dir/Dockerfile" ] || fail 'Run the installer from an extracted release or repository checkout'
    if [ "$consent" = ask ]; then
        [ -t 0 ] || fail 'Noninteractive install needs --auto-update or --no-auto-update'
        printf 'Automatically install newer stable Ternela releases while running? [y/N] '
        answer=''; read -r answer || true
        case $answer in y|Y|yes|YES) consent=true ;; *) consent=false ;; esac
    fi
    identity=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
    printf 'Building Ternela; the first build downloads its Python image and public API docs.\n'
    docker_cli build --tag "ternela-local:$identity" "$source_dir"
    mkdir -p "$(dirname -- "$install_dir")"
    mkdir "$install_dir" || fail 'Folder appeared during the build; refusing to overwrite it'
    mkdir "$install_dir/backups"
    printf 'native-v1\n' > "$install_dir/.ternela-storage"
    install_dir=$(CDPATH='' cd -- "$install_dir" && pwd -P)
    printf '%s\n' "$identity" > "$install_dir/.ternela-install"
    printf '%s\n' "$engine_id" > "$install_dir/.ternela-engine"
    printf '%s\n' "$docker_context" > "$install_dir/.ternela-context"
    cp "$source_dir/compose.yaml" "$install_dir/compose.yaml"
    cp "$source_dir/scripts/ternela.sh" "$install_dir/ternela.sh"
    cp "$source_dir/scripts/ternela.ps1" "$install_dir/ternela.ps1"
    cat > "$install_dir/docker.env" <<EOF
TERNELA_INSTALL_ID=$identity
TERNELA_PORT=$port
TERNELA_AUTO_UPDATE=$consent
EOF
fi

[ -f "$install_dir/.ternela-install" ] && [ ! -L "$install_dir/.ternela-install" ] || fail 'This is not a marked Ternela install folder'
[ ! -L "$install_dir/state" ] || fail 'The state folder must not be a symbolic link'
install_dir=$(CDPATH='' cd -- "$install_dir" && pwd -P)
home_dir=$(CDPATH='' cd -- "$HOME" && pwd -P)
[ "$install_dir" != / ] && [ "$install_dir" != "$home_dir" ] || fail 'Choose a dedicated install folder'
identity=$(cat "$install_dir/.ternela-install")
case $identity in ''|*[!0-9a-f]*) fail 'Invalid install marker' ;; esac
[ "${#identity}" -eq 16 ] || fail 'Invalid install marker'
[ -f "$install_dir/.ternela-engine" ] && [ ! -L "$install_dir/.ternela-engine" ] || fail 'Missing Docker engine marker; use the original script or reinstall into a new folder after backing up data'
current_engine=$(docker_cli info --format '{{.ID}}')
[ "$current_engine" = "$(cat "$install_dir/.ternela-engine")" ] || fail 'Docker engine differs from this installation; switch back to its original Docker context'
[ -f "$install_dir/.ternela-storage" ] && [ ! -L "$install_dir/.ternela-storage" ] && [ "$(cat "$install_dir/.ternela-storage")" = native-v1 ] || fail 'Legacy folder storage needs a verified migration before using this script'
volume="ternela-$identity-state"
volume_exists=$(docker_cli volume ls --quiet --filter "name=^$volume$")
if [ -n "$volume_exists" ]; then
    volume_owner=$(docker_cli volume inspect --format '{{index .Labels "io.ternela.install"}}' "$volume")
    [ "$volume_owner" = "$identity" ] || fail 'Live volume belongs to another installation; nothing changed'
else
    case $command_name in install|start|remove) ;; *) fail 'Live volume is missing; restore a backup before continuing' ;; esac
fi
# Saved settings win over an unrelated shell's Compose interpolation variables.
unset TERNELA_INSTALL_ID TERNELA_PORT TERNELA_UID TERNELA_GID TERNELA_AUTO_UPDATE TERNELA_STATE TERNELA_CONTAINER TERNELA_PUBLIC_PORT
compose() { docker_cli compose --project-directory "$install_dir" --env-file "$install_dir/docker.env" -f "$install_dir/compose.yaml" -p "ternela-$identity" "$@"; }
# Refuse a conflicting Compose project rather than stopping somebody else's container.
owners=$(docker_cli ps -a --filter "label=com.docker.compose.project=ternela-$identity" --format '{{.Label "com.docker.compose.project.working_dir"}}')
if [ -n "$owners" ]; then
    printf '%s\n' "$owners" | while IFS= read -r owner; do
        [ "$owner" = "$install_dir" ] || fail 'Another folder owns this container project'
    done
fi
control() { compose exec -T ternela python /opt/runtime/local_control.py "$@"; }
transfer() {
    docker_cli run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --user 1000:1000 --mount "type=volume,source=$volume,target=/state" --entrypoint python "ternela-local:$identity" /opt/runtime/storage_control.py "$1"
}
backup() {
    [ ! -L "$install_dir/backups" ] || fail 'Backup folder must not be a symbolic link'
    mkdir -p "$install_dir/backups"
    output="$install_dir/backups/state-$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz"
    [ ! -e "$output" ] && [ ! -e "$output.part" ] || fail 'Backup filename already exists'
    if transfer backup > "$output.part"; then
        mv "$output.part" "$output"
        printf 'Backup: %s\n' "$output"
    else
        rm -f "$output.part"
        return 1
    fi
}
case $command_name in
    install|start)
        compose up --detach --no-build --wait --wait-timeout 120 || fail 'Startup failed (or port is in use). Run logs; choose --port on a new install'
        saved_port=$(sed -n 's/^TERNELA_PORT=//p' "$install_dir/docker.env")
        printf '\nTernela: http://127.0.0.1:%s\nFolder: %s\nManage: sh "%s/ternela.sh" status\n' "$saved_port" "$install_dir" "$install_dir"
        ;;
    stop) compose stop ;;
    status) compose ps; control status ;;
    logs) compose logs --tail 100 ;;
    update) control update ;;
    updates) [ -n "$choice" ] || fail 'Use updates on or updates off'; control updates "$choice" ;;
    backup|restore)
        if [ "$command_name" = restore ]; then
            [ -n "$archive_file" ] && [ -f "$archive_file" ] && [ ! -L "$archive_file" ] || fail 'Use restore --file PATH with an existing backup'
        fi
        was_running=$(compose ps --status running --quiet)
        # The transfer process also acquires the native supervisor lock.
        compose stop
        resume() { if [ -n "$was_running" ]; then compose up --detach --no-build --wait --wait-timeout 120; fi; }
        trap 'result=$?; trap - EXIT; if ! resume; then exit 1; fi; exit "$result"' EXIT
        backup || fail 'Backup failed; original data retained'
        if [ "$command_name" = restore ]; then transfer restore < "$archive_file"; fi
        ;;
    remove)
        compose down
        if [ "$purge" = true ]; then
            if [ -n "$(docker_cli image ls --quiet --filter "reference=ternela-local:$identity")" ]; then
                docker_cli image rm "ternela-local:$identity" || fail 'Image is still in use; folder retained'
            fi
            if [ -n "$volume_exists" ]; then docker_cli volume rm "$volume" || fail 'Volume is in use; folder retained'; fi
            rm -rf -- "$install_dir"
            printf 'Removed the container, live volume, local image and marked install folder.\n'
        else
            printf 'Container removed. Live volume, folder and image retained; start restores the app. Use remove --purge to delete them.\n'
        fi
        ;;
esac
