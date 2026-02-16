#!/bin/sh
set -e

REPO="narora21/chrono-patient-uploader"
INSTALL_DIR="$HOME/chrono-uploader"
VERSION="${1:-latest}"

OS=$(uname -s)
case "$OS" in
  Darwin) PLATFORM="mac" ;;
  Linux)  PLATFORM="linux" ;;
  *)
    echo "Error: Unsupported OS '$OS'. Use install.ps1 for Windows."
    exit 1
    ;;
esac

ARCHIVE="chrono-uploader-${PLATFORM}.tar.gz"
if [ "$VERSION" = "latest" ]; then
  URL="https://github.com/${REPO}/releases/latest/download/${ARCHIVE}"
else
  URL="https://github.com/${REPO}/releases/download/${VERSION}/${ARCHIVE}"
fi

echo "Downloading chrono-uploader ${VERSION} for ${PLATFORM}..."
TMPDIR=$(mktemp -d)
curl -fSL "$URL" -o "$TMPDIR/$ARCHIVE"

echo "Installing to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"
tar -xzf "$TMPDIR/$ARCHIVE" -C "$INSTALL_DIR" --strip-components=1
rm -rf "$TMPDIR"

chmod +x "$INSTALL_DIR/chrono-uploader"

echo "Verifying installation..."
if ! "$INSTALL_DIR/chrono-uploader" --help > /dev/null 2>&1; then
  echo "Error: Installation verification failed."
  exit 1
fi

# Add to PATH
SHELL_NAME=$(basename "$SHELL")
case "$SHELL_NAME" in
  zsh)  RC_FILE="$HOME/.zshrc" ;;
  bash) RC_FILE="$HOME/.bashrc" ;;
  *)    RC_FILE="" ;;
esac

PATH_LINE="export PATH=\"$INSTALL_DIR:\$PATH\""

if [ -n "$RC_FILE" ]; then
  if ! grep -qF "$INSTALL_DIR" "$RC_FILE" 2>/dev/null; then
    echo "" >> "$RC_FILE"
    echo "# chrono-uploader" >> "$RC_FILE"
    echo "$PATH_LINE" >> "$RC_FILE"
    echo "Added chrono-uploader to PATH in $RC_FILE"
  else
    echo "chrono-uploader already in PATH ($RC_FILE)"
  fi
else
  echo "Could not detect shell config file. Add this to your shell profile manually:"
  echo "  $PATH_LINE"
fi

echo ""
echo "chrono-uploader installed successfully!"
echo ""
echo "Restart your terminal or run:"
echo "  $PATH_LINE"
echo ""
echo "Then use:"
echo "  chrono-uploader <directory>"
