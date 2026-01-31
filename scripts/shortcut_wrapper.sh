#!/bin/bash
# flyingphase iOS Shortcut wrapper
# Called via "Run Script over SSH" from iOS Shortcuts
#
# Usage: ./shortcut_wrapper.sh "<METAR>" [<TAF>] [--bird moderate] [--notices "..."] [--no-notams] [--verbose]
#
# If no arguments, prints help.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ $# -eq 0 ]; then
    echo "Paste a METAR to determine the KFAA flying phase."
    echo ""
    echo "Usage: shortcut_wrapper.sh \"<METAR>\" [TAF] [options]"
    echo ""
    echo "Options:"
    echo "  --bird low|moderate|severe"
    echo "  --notices \"text\""
    echo "  --no-notams    Skip NOTAM fetch"
    echo "  --verbose      Show weather pipeline details"
    exit 0
fi

python3 "$SCRIPT_DIR/flyingphase.py" "$@"
