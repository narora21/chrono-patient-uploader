#!/bin/sh
set -e

# Usage: ./scripts/release.sh [--major | --minor | --patch]
# Default: patch bump

BUMP="patch"
for arg in "$@"; do
  case "$arg" in
    --major) BUMP="major" ;;
    --minor) BUMP="minor" ;;
    --patch) BUMP="patch" ;;
    *) echo "Usage: $0 [--major | --minor | --patch]"; exit 1 ;;
  esac
done

# Get latest tag (across all branches, not just current)
LATEST=$(git tag --sort=-v:refname | head -n1)
LATEST=${LATEST:-v0.0.0}
echo "Current version: $LATEST"

# Parse version
VERSION="${LATEST#v}"
MAJOR=$(echo "$VERSION" | cut -d. -f1)
MINOR=$(echo "$VERSION" | cut -d. -f2)
PATCH=$(echo "$VERSION" | cut -d. -f3)

# Bump
case "$BUMP" in
  major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
  patch) PATCH=$((PATCH + 1)) ;;
esac

NEW_TAG="v${MAJOR}.${MINOR}.${PATCH}"
NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
BRANCH="release/${NEW_TAG}"
echo "New version: $NEW_TAG"

# Update version.py on main first
VERSION_FILE="src/version.py"
if [ -f "$VERSION_FILE" ]; then
  sed -i.bak "s/__version__ = \".*\"/__version__ = \"$NEW_VERSION\"/" "$VERSION_FILE"
  rm -f "${VERSION_FILE}.bak"
  git add "$VERSION_FILE"
  git commit -m "Bump version to $NEW_VERSION"
  echo "Updated $VERSION_FILE on main"
else
  echo "Warning: $VERSION_FILE not found, skipping version update in source"
fi

# Create release branch from the version bump commit
git checkout -b "$BRANCH"
echo "Created branch: $BRANCH"

# Tag and push branch + tag, then update main
git tag "$NEW_TAG"
git push origin "$BRANCH" "$NEW_TAG"
git checkout main
git push origin main

echo ""
echo "Released $NEW_TAG on branch $BRANCH"
echo "GitHub Actions will now build and publish the release."
