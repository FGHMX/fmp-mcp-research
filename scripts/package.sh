#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
zip -r ../fmp-mcp-research.zip . -x "*.env" "*.venv*" "*__pycache__*"
