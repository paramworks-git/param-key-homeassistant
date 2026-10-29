#!/bin/sh
# Run the HA package tests in the official Home Assistant container.
# HOST_DIR: this directory as the Docker daemon sees it (differs when Docker runs
# on another host, e.g. Unraid).
set -e
HOST_DIR="${HOST_DIR:-$(cd "$(dirname "$0")" && pwd)}"
exec docker run --rm -v "$HOST_DIR":/ha:ro --entrypoint python3 \
  ghcr.io/home-assistant/home-assistant:stable /ha/tests/test_package.py
