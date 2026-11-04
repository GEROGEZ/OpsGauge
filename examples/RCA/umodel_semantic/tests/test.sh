#!/bin/sh
set -eu
mkdir -p /logs/verifier
python3 /tests/verify.py | tee /logs/verifier/test-stdout.txt
