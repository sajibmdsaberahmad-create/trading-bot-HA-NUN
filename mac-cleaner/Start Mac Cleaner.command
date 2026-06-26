#!/bin/bash
# Double-click in Finder to open the Mac Storage Cleaner menu.
cd "$(dirname "$0")"
bash menu.sh
echo ""
read -r -p "Press Enter to close…" _
