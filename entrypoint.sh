#!/bin/bash
set -e

# Ensure volumes are writable by the app user
mkdir -p /app/repo /app/tmp
chown -R app:app /app/repo /app/tmp

# Drop privileges and run the CMD
exec gosu app "$@"
