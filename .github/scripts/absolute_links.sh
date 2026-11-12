#!/usr/bin/env bash
# Rewrite the relative links of a release text (written in docs/releases/) as
# absolute links to the files on main, so they work on the GitHub release page.
# Usage: absolute_links.sh <file> <owner/repo>
set -euo pipefail
file="$1"
base="https://github.com/$2/blob/main/docs"
sed -E -i \
  -e "s#\]\(\.\./([^):]*)\)#](${base}/\1)#g" \
  -e "s#\]\((\./)?([A-Za-z0-9_][^):]*)\)#](${base}/releases/\2)#g" \
  "$file"
