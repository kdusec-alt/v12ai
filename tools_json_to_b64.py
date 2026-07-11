# -*- coding: utf-8 -*-
"""Convert Google service-account JSON to one-line Base64 for Streamlit Secrets.

Usage:
python tools_json_to_b64.py path/to/service_account.json
"""
from __future__ import annotations
import base64
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("Usage: python tools_json_to_b64.py service_account.json")
    raise SystemExit(1)

p = Path(sys.argv[1])
raw = p.read_bytes()
print(base64.b64encode(raw).decode("ascii"))
