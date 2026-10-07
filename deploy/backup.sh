#!/bin/sh
# Nightly copy of Natro's memory and notes, run by cron as the natro user:
#   30 3 * * * /home/natro/natro/deploy/backup.sh
# Keeps 14 days in ~/backups. (Syncthing also keeps both folders on the PC.)
set -e
cd "$HOME/natro"
mkdir -p "$HOME/backups"
tar czf "$HOME/backups/natro-$(date +%F).tar.gz" --exclude=.stversions memory notes
find "$HOME/backups" -name 'natro-*.tar.gz' -mtime +14 -delete
