#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Reference runner: real processes, artifact, evidence and handoff; no model call.
set -eu
umask 077
root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
command -v docker >/dev/null 2>&1 || { printf 'Start Docker Desktop or Colima with Docker + Compose first.\n' >&2; exit 1; }
context=$(docker context show)
[ -n "$context" ] || { printf 'Select a named Docker context first.\n' >&2; exit 1; }
docker_cli() { docker --context "$context" "$@"; }
identity=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
output="$root/data/demos/container-$identity"
image=ternela-agent-demo:local
container="ternela-agent-demo-$identity"
cid=''
mkdir -p "$output"
cleanup() {
    result=$?
    trap - EXIT
    if [ -n "$cid" ]; then docker_cli rm --force --volumes "$cid" >/dev/null 2>&1 || true; fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Building the demo image (cached after the first run).\n'
docker_cli build --label io.ternela.purpose=agent-demo --tag "$image" "$root"
code='import pathlib,sys,shutil,subprocess
sys.path.insert(0,"/opt/runtime")
from distribution import extract_bundle
source=pathlib.Path("/tmp/ternela-demo")
import tarfile,json
with tarfile.open("/opt/bootstrap.tar.gz") as archive:
 record=json.load(archive.extractfile("release.json"))
extract_bundle(pathlib.Path("/opt/bootstrap.tar.gz"),source,record["repository"],record["version"])
shutil.copytree(source/"seed",source/"data")
raise SystemExit(subprocess.run([sys.executable,"-m","examples.agent_handoff","--dir","/state/demo"],cwd=source).returncode)'
cid=$(docker_cli create --name "$container" --label io.ternela.purpose=agent-demo \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --tmpfs /tmp:size=128m,mode=1777 --volume /state --no-healthcheck --entrypoint python "$image" -c "$code")
docker_cli start --attach "$cid" || true
result=$(docker_cli inspect --format '{{.State.ExitCode}}' "$cid")
docker_cli logs "$cid" > "$output/run.log" 2>&1
gzip "$output/run.log"
if ! docker_cli cp "$cid:/state/demo/." "$output/"; then
    printf 'Could not export demo artifacts; logs retained in %s\n' "$output" >&2
    exit 1
fi
[ "$result" -eq 0 ] || { printf 'Demo failed; logs retained in %s\n' "$output" >&2; exit 1; }
printf '\nSaved actual CSV, answer, timeline and queue state: %s\n' "$output"
printf 'This reference run made no model calls. For a real Codex session, see docs/agent-demo.md.\n'
