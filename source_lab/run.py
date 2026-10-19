# SPDX-License-Identifier: Apache-2.0
"""Run the complete hardened compiler with versioned supplementary documentation."""
import json
from expansion_lab import run as compiler
from expansion_lab.storage import PROJECT, sha
from safety_lab import run as hardened
from .evidence import SourceIndex, MANIFEST


def main():
    compiler.SourceIndex = SourceIndex
    previous = compiler.code_manifest
    def code_manifest():
        code = previous()
        for path in (PROJECT / "source_lab").glob("*.py"):
            code[str(path.relative_to(PROJECT))] = sha(path.read_bytes())
        code[str(MANIFEST.relative_to(PROJECT))] = sha(MANIFEST.read_bytes())
        return code
    compiler.code_manifest = code_manifest
    hardened.CONTRACT += "\nSUPPLEMENTARY EVIDENCE: operation-local response shapes may be inferred from optional fields in a separately hashed official JSON example. Their descriptions identify the source URL. A shape is not a required-field guarantee: missing evidence at runtime remains unknown. Do not claim any unobserved semantic guarantee.\n"
    hardened.main()


if __name__ == "__main__": main()
