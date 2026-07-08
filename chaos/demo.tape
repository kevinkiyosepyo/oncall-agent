# vhs recording script for the README demo GIF.
# Usage (from the repo root, with the stack running):  vhs chaos/demo.tape
Output examples/demo.gif

Set FontSize 14
Set Width 1200
Set Height 700
Set Padding 12
Set TypingSpeed 40ms

# Clean slate: resolve any open incident for this scenario, reset faults.
Hide
Type "python3 chaos/inject.py high-error-rate --resolve-only >/dev/null 2>&1; python3 chaos/inject.py reset >/dev/null 2>&1; clear"
Enter
Sleep 4s
Show

# Stage the incident: bad commit, degraded service, alert fired.
Type "python3 chaos/inject.py high-error-rate"
Enter
Sleep 16s

# Analysis runs in the agent (~25s); skip the wait in the recording.
Hide
Type "sleep 35; clear"
Enter
Sleep 37s
Show

# The incident brief the agent produced.
Type "./chaos/latest-brief.sh"
Enter
Sleep 10s
