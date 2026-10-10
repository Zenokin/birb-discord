#!/bin/bash

echo "Make sure you are logged in with:
gh auth login --web
gh auth refresh -h github.com -s read:packages,delete:packages
"

PKG=$1

: "${PKG:?PACKAGE input not set}"

gh api --paginate /user/packages/container/$PKG/versions \
	--jq '.[] | select(.metadata.container.tags | length == 0) | .id' |
	while read id; do
		gh api -X DELETE /user/packages/container/$PKG/versions/$id
	done
