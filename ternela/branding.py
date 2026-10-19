# SPDX-License-Identifier: Apache-2.0
"""Working product identity; imports and stored data keep a stable namespace."""

import json
from pathlib import Path

BRAND_PATH = Path(__file__).with_name("brand.json")
BRAND = json.loads(BRAND_PATH.read_text())
NAME = BRAND["name"]
