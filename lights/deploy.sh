#!/usr/bin/env bash
# Copy the controller, logger, monitoring page, network service and light check to the Pi and restart them.
#
# The live schedule is /var/lib/tenebrio-lights/config.yaml, which the monitoring page
# edits. A deploy seeds it from config.yaml only if it does not exist yet; pass
# --push-config to overwrite it with this folder's config.yaml (logged in the event log).
# Any difference between the two is printed so that neither drifts unnoticed.
#
# Restarting does not blink the lights: the HAT holds each relay while the new process
# starts, and the new process sets the same scheduled state. The final sync flushes the
# new files to the SD card: on 2026-10-09 a power cut seconds after a deploy left the
# installed unit file empty, which systemd reads as masked.
set -euo pipefail
cd "$(dirname "$0")"
host=tenebrio push_config=false
for arg in "$@"; do
  case "$arg" in
    --push-config) push_config=true ;;
    *) host="$arg" ;;
  esac
done

python3 -c 'import yaml' 2>/dev/null && python3 tenebrio_lights.py --config config.yaml --check >/dev/null
ssh "$host" 'sudo install -d -o pi -g pi /opt/tenebrio-lights /var/lib/tenebrio-lights'
rsync -a --delete --exclude '__pycache__' --exclude 'test_*' --exclude '.pytest_cache' ./ "$host":/opt/tenebrio-lights/
ssh "$host" "push=$push_config"' bash -s' <<'REMOTE'
set -euo pipefail
live=/var/lib/tenebrio-lights/config.yaml
repo=/opt/tenebrio-lights/config.yaml
python3 /opt/tenebrio-lights/tenebrio_lights.py --config "$repo" --check >/dev/null
if [ ! -e "$live" ]; then
  install -m 644 "$repo" "$live"; echo "seeded $live from config.yaml"
elif [ "$push" = true ]; then
  install -m 644 "$repo" "$live"
  printf '%s,,,%s\n' "$(date -Iseconds)" "config replaced by deploy --push-config" >> /var/lib/tenebrio-lights/events.csv
  echo "replaced $live with config.yaml"
else
  # Compare values, not text: the page writes the file without config.yaml's comments.
  python3 - "$repo" "$live" <<'PY'
import sys, yaml
repo, live = (yaml.safe_load(open(path)) for path in sys.argv[1:])
if repo != live:
    print("NOTE: the live schedule differs from config.yaml (edited on the monitoring page?)")
    for key in sorted(set(repo) | set(live)):
        if repo.get(key) != live.get(key):
            print(f"  {key}: config.yaml={repo.get(key)!r}  live={live.get(key)!r}")
PY
fi
sudo install -m 644 /opt/tenebrio-lights/tenebrio-wifi-powersave.conf /etc/NetworkManager/conf.d/
sudo nmcli general reload conf
# The setting applies when a connection next starts; switch it off on the running one too.
sudo /usr/sbin/iw dev wlan0 set power_save off
sudo install -m 644 /opt/tenebrio-lights/tenebrio-{lights,logger,web,network,validator}.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --quiet tenebrio-lights tenebrio-logger tenebrio-web tenebrio-network tenebrio-validator
sudo systemctl restart tenebrio-lights tenebrio-logger tenebrio-web tenebrio-network tenebrio-validator
sync
systemctl is-active tenebrio-lights tenebrio-logger tenebrio-web tenebrio-network tenebrio-validator | paste -sd ' ' -
python3 /opt/tenebrio-lights/tenebrio_lights.py --config "$live" --check
REMOTE
