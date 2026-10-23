#!/usr/bin/env bash
# Run a shell command on the agent instance through SSM (there is no SSH) and print its output.
#
#   infra/compute/run.sh 'cd app && ~/.local/bin/uv run --env-file .env python -m agent.demo --guard full --warm --no-save | tail -40'
#
# The command runs as the phishguard user in /opt/phishguard. Needs the stack applied and the
# AWS_PROFILE / AWS_REGION of the README. SSM keeps the last 24,000 characters of output.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
id="$(terraform -chdir="$here" output -raw instance_id)"
params="$(python3 -c '
import json, shlex, sys
inner = "cd /opt/phishguard && " + sys.argv[1]
print(json.dumps({"commands": ["sudo -u phishguard -H bash -lc " + shlex.quote(inner)]}))
' "$1")"

command_id="$(aws ssm send-command --instance-ids "$id" --document-name AWS-RunShellScript \
  --parameters "$params" --timeout-seconds 3600 --query Command.CommandId --output text)"

# Stop waiting a little after SSM's own 3600 s timeout, even if the status can't be read.
deadline=$(($(date +%s) + 3660))
while true; do
  status="$(aws ssm get-command-invocation --command-id "$command_id" --instance-id "$id" \
    --query Status --output text 2>/dev/null || echo Pending)"
  case "$status" in
    Pending | InProgress | Delayed) ;;
    *) break ;;
  esac
  if [ "$(date +%s)" -gt "$deadline" ]; then
    echo "Gave up waiting for SSM command $command_id (last status: $status)" >&2
    exit 1
  fi
  sleep 10
done

aws ssm get-command-invocation --command-id "$command_id" --instance-id "$id" \
  --query StandardOutputContent --output text
aws ssm get-command-invocation --command-id "$command_id" --instance-id "$id" \
  --query StandardErrorContent --output text >&2
echo "SSM status: $status" >&2
[ "$status" = Success ]
