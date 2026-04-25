#!/usr/bin/env bash
# scripts/setup-v2-worktrees.sh
#
# Creates per-track git worktrees for parallel V2 construction.
# Each worktree gets its own physical directory and branch.
# All worktrees share the same .git/ (so branches are visible across them).
#
# Usage:
#   bash scripts/setup-v2-worktrees.sh
#
# To remove a worktree later:
#   git worktree remove ~/nexus-platform-trackE
set -euo pipefail

WORKTREES=(
  "trackE:overwatch-v2-day-2-schema"
  "trackF:overwatch-v2-day-2-tools"
  "trackG:overwatch-v2-day-2-pipeline-truth"
)

cd "$(git rev-parse --show-toplevel)"

for entry in "${WORKTREES[@]}"; do
  IFS=':' read -r name branch <<< "$entry"
  path="$HOME/nexus-platform-$name"

  if [ -d "$path" ]; then
    echo "skip: $path already exists"
    continue
  fi

  if git show-ref --verify --quiet "refs/heads/$branch"; then
    git worktree add "$path" "$branch"
  else
    git worktree add "$path" -b "$branch" main
  fi
  echo "created: $path on branch $branch"
done

echo
echo "=== Worktrees ==="
git worktree list
