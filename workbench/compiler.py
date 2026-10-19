# SPDX-License-Identifier: Apache-2.0
"""Compile one caller task with the same tool-disabled, bounded retrieval pipeline."""
import json
import sys
from expansion_lab import run as original
from source_lab.run import main

if __name__ == "__main__":
    # Jobs write a public fixture first. Private fixture is empty and never enters inference.
    original.FIXTURES["workbench"] = "data/tasks/" + sys.argv[1]
    sys.argv = [sys.argv[0],"--arm","adaptive","--suite","workbench","--run-id",sys.argv[1]]
    main()
