#!/bin/bash
cd "$(dirname "$0")"
bash menu.sh scan
echo ""
read -r -p "Press Enter to close…" _
