#!/usr/bin/env bash
# Fallback when setup-python has no stable 3.15 binary for the hosted runner.
set -Eeuo pipefail
PREFIX="${1:?Usage: setup_python315.sh INSTALL_PREFIX}"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
curl -fL --retry 3 https://www.python.org/ftp/python/3.15.0/Python-3.15.0.tar.xz \
  -o "$BUILD_DIR/python.tar.xz"
printf '%s  %s\n' ba4bed1ba346b916890b76d9e320451420aa69f6408997d33c66482eeae3d575 \
  "$BUILD_DIR/python.tar.xz" | sha256sum -c -
tar --no-same-owner -xf "$BUILD_DIR/python.tar.xz" -C "$BUILD_DIR"
cd "$BUILD_DIR/Python-3.15.0"
./configure --prefix="$PREFIX" --with-ensurepip=install
make -j2
make install
ln -s python3.15 "$PREFIX/bin/python"
"$PREFIX/bin/python" -c 'import sys, ssl, sqlite3, zlib, bz2, lzma, ctypes; assert sys.version_info[:3] == (3, 15, 0) and sys.version_info.releaselevel == "final", sys.version'
