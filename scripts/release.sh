#!/usr/bin/env bash
# Release helper: bump __version__, merge a feature branch into main, tag, push.
# The GitHub tag-triggered build takes it from there.
#
#   scripts/release.sh                    # patch bump of the current branch
#   scripts/release.sh minor              # 0.2.1 -> 0.3.0
#   scripts/release.sh major              # 0.2.1 -> 1.0.0
#   scripts/release.sh 0.4.2              # explicit version
#   scripts/release.sh minor -b my-feat   # release a branch other than HEAD
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
INIT="src/mtgo_overlay/__init__.py"
MAIN="main"

level="patch"
branch="$(git rev-parse --abbrev-ref HEAD)"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -b|--branch) branch="$2"; shift 2 ;;
    -h|--help) echo "Usage: $0 [patch|minor|major|X.Y.Z] [-b BRANCH]"; exit 0 ;;
    *) level="$1"; shift ;;
  esac
done

[[ "$branch" == "$MAIN" ]] && { echo "Refusing to release $MAIN into itself; pass a feature branch." >&2; exit 1; }
git rev-parse -q --verify "refs/heads/$branch" >/dev/null || { echo "No such branch: $branch" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "Working tree not clean." >&2; exit 1; }
git checkout -q "$branch"

cur="$(grep -oP '__version__ = "\K[^"]+' "$INIT")"
if [[ "$level" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  new="$level"
else
  IFS=. read -r ma mi pa <<<"$cur"
  case "$level" in
    major) new="$((ma + 1)).0.0" ;;
    minor) new="$ma.$((mi + 1)).0" ;;
    patch) new="$ma.$mi.$((pa + 1))" ;;
    *) echo "Usage: $0 <patch|minor|major|X.Y.Z>" >&2; exit 1 ;;
  esac
fi
tag="v$new"
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && { echo "Tag $tag already exists." >&2; exit 1; }

echo "Release $cur -> $new   (merge $branch into $MAIN, tag $tag)"
read -rp "Proceed? [y/N] " ok; [[ "$ok" == [yY] ]] || exit 1

sed -i "s/__version__ = \"$cur\"/__version__ = \"$new\"/" "$INIT"
QT_QPA_PLATFORM=offscreen uv run pytest tests/ -q
git commit -aqm "Bump version to $new"

git checkout -q "$MAIN"
git pull -q --ff-only
git merge --no-ff -q "$branch" -m "Merge $branch: release $tag"
git tag -a "$tag" -m "$tag"
git push -q origin "$MAIN"
git push -q origin "$tag"
echo "Pushed $MAIN and $tag."
