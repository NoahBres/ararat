#!/bin/bash
# Bidirectional sync of private-data/ between this machine and rtk.local.
# Pulls from remote first, then pushes — newer file wins on conflict.

LOCAL="$HOME/Developer/ararat/private-data/"
REMOTE="noah@rtk.local:~/Developer/ararat/private-data/"

rsync -a --update "$REMOTE" "$LOCAL" && \
rsync -a --update "$LOCAL" "$REMOTE"
