#!/bin/bash
set -e
if grep "### Custom report" custom.txt >/dev/null; then
  echo "Custom report was created"
else
  echo "We failed to create a custom report"
  exit 1
fi
