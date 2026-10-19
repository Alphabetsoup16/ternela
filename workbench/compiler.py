# SPDX-License-Identifier: Apache-2.0
"""Compile one local task with bounded retrieval and public validation feedback."""

import argparse
import re

from expansion_lab.run import main as compile_task
from safety_lab.run import CONTRACT, HardenedEvidence
from source_lab.evidence import SourceIndex


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    job_id = parser.parse_args().job_id
    if not re.fullmatch(r"task-[a-f0-9]{32}", job_id):
        parser.error("Expected a local compiler job ID")
    compile_task(
        ["--arm", "adaptive", "--suite", "workbench", "--run-id", job_id],
        fixtures={"workbench": "data/tasks/" + job_id},
        index_class=SourceIndex,
        evidence_class=HardenedEvidence,
        extra_instructions=CONTRACT,
    )


if __name__ == "__main__":
    main()
