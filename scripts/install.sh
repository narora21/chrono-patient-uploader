#!/bin/sh
set -e

REPO="narora21/chrono-patient-uploader"
INSTALL_DIR="$HOME/chrono-uploader"

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
URL="https://github.com/${REPO}/releases/latest/download/${ARCHIVE}"

echo "Downloading chrono-uploader for ${PLATFORM}..."
TMPDIR=$(mktemp -d)
curl -fSL "$URL" -o "$TMPDIR/$ARCHIVE"

echo "Installing to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"
tar -xzf "$TMPDIR/$ARCHIVE" -C "$INSTALL_DIR" --strip-components=1
rm -rf "$TMPDIR"

chmod +x "$INSTALL_DIR/chrono-uploader"

echo "Verifying installation..."
if "$INSTALL_DIR/chrono-uploader" --help > /dev/null 2>&1; then
  echo ""
  echo "chrono-uploader installed successfully!"
  echo ""
  echo "Location: $INSTALL_DIR/chrono-uploader"
  echo ""
  echo "To run it:"
  echo "  $INSTALL_DIR/chrono-uploader <directory>"
  echo ""
  echo "Or add it to your PATH:"
  echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
else
  echo "Error: Installation verification failed."
  exit 1
fi
