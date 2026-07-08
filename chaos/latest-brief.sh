#!/bin/sh
# Print the most recent incident brief from the agent's logs.
# Handy for demos and recordings: run a scenario, then run this.
set -e
cd "$(dirname "$0")/.."

docker compose logs agent 2>/dev/null \
  | sed 's/^agent-1  | //' \
  | awk '
      /INCIDENT BRIEF:/ { buf = "=============================================================="; sep = 0 }
      buf != "" && !/INCIDENT BRIEF:/ { buf = buf "\n" $0 }
      buf != "" && /INCIDENT BRIEF:/ { buf = buf "\n" $0 }
      /^=+$/ && buf != "" { sep++; if (sep == 2) { latest = buf; buf = "" } }
      END { if (latest != "") print latest; else print "no brief in agent logs yet" }
    '
