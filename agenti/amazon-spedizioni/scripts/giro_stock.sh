#!/bin/bash
# Giro quotidiano dello stock (DESIGN §14). Lanciato dal LaunchAgent alle 11:00.
# Python puro, niente claude -p: qui non c'è niente da giudicare.
cd "$(dirname "$0")" || exit 1
exec /usr/bin/python3 giro.py stock
