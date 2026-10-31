#!/bin/sh
# Run the package and integration tests in the official Home Assistant container.
# HOST_DIR: this directory as the Docker daemon sees it (differs when Docker runs
# on another host, e.g. a NAS).
set -e
HOST_DIR="${HOST_DIR:-$(cd "$(dirname "$0")" && pwd)}"
exec docker run --rm -v "$HOST_DIR":/ha:ro --entrypoint sh ghcr.io/home-assistant/home-assistant:stable -c \
  'python3 /ha/tests/test_package.py && python3 /ha/tests/test_integration.py'
