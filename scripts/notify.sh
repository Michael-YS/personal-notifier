#!/usr/bin/env bash
set +ax
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bash notify.sh --title TITLE --body BODY [--source SOURCE] [--device DEVICE]
                      [--urgent true|false] [--ttl-seconds SECONDS]

Requires curl and Python 3. Set these unquoted values in .notify.env beside the script:
  NOTIFY_ENDPOINT=https://notify.example.com/notify
  NOTIFY_PUSH_KEY=your-push-key

Omit --device to broadcast. Omitted optional flags use the server defaults.
Prints the response. Exits successfully only when the server reports accepted.
EOF
}

fail() {
    printf 'notify: %s\n' "$1" >&2
    exit 1
}

args=()
while (($#)); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --title|--body|--source|--device|--urgent|--ttl-seconds)
            (($# >= 2)) || fail 'An option is missing its value'
            args+=("$1" "$2")
            shift 2
            ;;
        *) fail 'Unknown argument. Run with --help for usage.' ;;
    esac
done

command -v curl >/dev/null || fail 'curl is required'
command -v python3 >/dev/null || fail 'Python 3 is required'

payload=$(python3 - "${args[@]}" <<'PY'
import json
import re
import sys

fields = {flag[2:].replace("-", "_"): value for flag, value in zip(sys.argv[1::2], sys.argv[2::2])}
if not fields.get("title") or not fields.get("body"):
    sys.exit("notify: --title and --body are required and must be nonempty")
if "urgent" in fields:
    if fields["urgent"] not in ("true", "false"):
        sys.exit("notify: --urgent must be true or false")
    fields["urgent"] = fields["urgent"] == "true"
if "ttl_seconds" in fields:
    ttl = fields["ttl_seconds"]
    if not re.fullmatch(r"[0-9]{1,7}", ttl) or int(ttl) > 2419200:
        sys.exit("notify: --ttl-seconds must be an integer between 0 and 2419200")
    fields["ttl_seconds"] = int(ttl)
print(json.dumps(fields))
PY
)

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
config=$script_dir/.notify.env
[[ -r $config ]] || fail 'Create .notify.env beside the script. Run with --help for an example.'
unset line notify_endpoint notify_push_key
while IFS= read -r line || [[ -n $line ]]; do
    line=${line%$'\r'}
    case "$line" in
        NOTIFY_ENDPOINT=*) notify_endpoint=${line#*=} ;;
        NOTIFY_PUSH_KEY=*) notify_push_key=${line#*=} ;;
    esac
done < "$config"

[[ ${notify_endpoint:-} == http://?* || ${notify_endpoint:-} == https://?* ]] ||
    fail 'Set NOTIFY_ENDPOINT to the full HTTP(S) notify URL in .notify.env'
[[ ${notify_push_key:-} =~ ^[A-Za-z0-9._~+/=-]+$ ]] ||
    fail 'Set NOTIFY_PUSH_KEY to the unquoted push key in .notify.env'

response=$(printf 'Authorization: Bearer %s\n' "$notify_push_key" |
    curl --disable --silent --show-error --globoff --proto '=http,https' \
        --connect-timeout 10 --max-time 60 --header @- --header 'Content-Type: application/json' \
        --data-binary "$payload" --write-out $'\n%{http_code}' -- "$notify_endpoint") ||
    fail 'Request failed. Delivery may be unknown.'

http_status=${response##*$'\n'}
response=${response%$'\n'*}
printf '%s\n' "$response"
[[ $http_status == 2[0-9][0-9] ]] || fail "Notify endpoint returned HTTP $http_status"

if ! printf '%s' "$response" | python3 -c '
import json
import sys

try:
    response = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
sys.exit(0 if isinstance(response, dict) and response.get("status") == "accepted" else 1)
'; then
    fail 'Notify response did not report accepted. Check the response for details.'
fi
