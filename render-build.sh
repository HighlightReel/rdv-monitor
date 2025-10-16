#!/usr/bin/env bash
set -e
pip install -r requirements.txt
# install browsers into the project directory that gets deployed
export PLAYWRIGHT_BROWSERS_PATH=0
python -m playwright install chromium
