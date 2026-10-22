# SPDX-License-Identifier: Apache-2.0
"""Offline compiler qualification using a local CLI stand-in; never invokes a model."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ternela.paths import CHECKOUT, PROJECT, resource
from ternela.storage import Store, canonical, sha

# The stand-in exercises real stdin, schema/output files and event capture. It
# performs no model request or business action, even when reporting a tool event.
CLI = r"""#!{python}
import json,os,sys
from pathlib import Path
if sys.argv[1:] == ['--version']:
 print('offline compiler fixture 1');sys.exit(0)
config=Path(os.environ['TERNELA_COMPILER_FIXTURE'])
cfg=json.loads(config.read_text())
state=config.with_suffix('.calls.jsonl')
prompt=sys.stdin.read()
with state.open('a') as log:
 log.write(json.dumps(dict(argv=sys.argv[1:],prompt=prompt))+'\n')
number=len(state.read_text().splitlines())
if number <= cfg.get('operational_failures',0):
 print('injected operational failure',file=sys.stderr);sys.exit(5)
schema=json.loads(Path(sys.argv[sys.argv.index('--output-schema')+1]).read_text())
payload=json.loads(prompt.split('\n\nINPUT:\n',1)[1])
if 'operations' in schema['properties']['plans']['items']['properties']:
 result={{'plans':[{{'task_id':t['id'],'operations':[r['operation'] for r in cfg['batch']['plans'][0]['requests']],'schema_reads':[],'rationale':'Offline fixture selects its documented operation'}} for t in payload['tasks']]}}
else:
 result=cfg['batch']
 for plan,task in zip(result['plans'],payload['tasks']):plan['task_id']=task['id']
mode=cfg.get('mode')
if mode=='outside' and 'operations' in result['plans'][0]:result['plans'][0]['operations']=['GET /not-in-supplied-evidence']
if mode=='repair' and number==2:result['plans'][0]['requirements'][0]['check_ids']=[]
if mode=='admission' and 'requests' in result['plans'][0]:
 result['plans'][0]['requirements'][0]['check_ids']=[]
 result['plans'][0]['retrieval_needs']=[{{'kind':'operation','query':'missing required metadata','operation':'','section':'response','pointer':'','reason':'Offline test forces unresolved evidence'}}]
if mode=='shape':result={{'plans':'invalid'}}
if mode=='duplicate':result={{'plans':result['plans']*2}}
if mode=='identity':result={{'plans':[dict(result['plans'][0],task_id='wrong-task')]}}
output=Path(sys.argv[sys.argv.index('-o')+1])
output.write_text('not json' if mode=='json' else json.dumps(result))
if mode=='tool':print(json.dumps({{'item':{{'type':'command_execution'}}}}))
print(json.dumps({{'usage':{{'input_tokens':0,'output_tokens':0}}}}))
"""


class Fixture:
    def __init__(self, root, *, mode=None, operational_failures=0):
        self.root = Path(root)
        self.data = self.root / "data"
        self.bin = self.root / "bin"
        self.bin.mkdir(parents=True)
        self.job_id = "task-" + "a" * 32
        template = next(
            t
            for t in json.loads(resource("examples/catalog.json").read_text())
            if t["task"]["id"] == "live-github-demo-1"
        )
        self.task = dict(template["task"], id=self.job_id)
        self.plan = dict(template["plan"], task_id=self.job_id)
        self.batch = {"plans": [self.plan]}
        self.config = self.root / "fixture.json"
        self.config.write_text(
            json.dumps(dict(batch=self.batch, mode=mode, operational_failures=operational_failures))
        )
        executable = self.bin / "codex"
        executable.write_text(CLI.format(python=sys.executable))
        executable.chmod(0o700)
        self.store = Store(self.data)
        directory = self.data / "tasks" / self.job_id
        directory.mkdir(parents=True)
        files = {
            "public.json": canonical({"tasks": [self.task]}),
            "private.json": canonical({"oracle": "PRIVATE-ORACLE-MUST-NEVER-ENTER-INFERENCE"}),
        }
        for name, raw in files.items():
            (directory / name).write_bytes(raw)
        (directory / "manifest.json").write_bytes(canonical({n: sha(v) for n, v in files.items()}))
        self.env = dict(
            os.environ,
            TERNELA_DATA=str(self.data),
            TERNELA_COMPILER_FIXTURE=str(self.config),
            PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            PYTHONDONTWRITEBYTECODE="1",
        )
        self.env.pop("PYTHONPATH", None)
        self.env.pop("PYTHONHOME", None)

    def calls(self):
        path = self.config.with_suffix(".calls.jsonl")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run(self, module="ternela.compiler", *, cwd=None):
        return subprocess.run(
            [sys.executable, "-B", "-m", module, self.job_id],
            cwd=cwd or (PROJECT if CHECKOUT else self.root),
            env=self.env,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def manifest(self):
        return json.loads((self.data / "runs" / self.job_id / "manifest.json").read_text())


def smoke():
    with tempfile.TemporaryDirectory(prefix="ternela-compiler-") as temporary:
        fixture = Fixture(temporary)
        completed = fixture.run()
        if completed.returncode:
            raise AssertionError(completed.stdout + completed.stderr)
        manifest = fixture.manifest()
        assert manifest["status"] == "completed" and manifest["admission_status"] == "accepted"
        assert fixture.store.get(manifest["plans"]) == fixture.batch
        calls = fixture.calls()
        assert 2 <= len(calls) <= 4
        assert all("PRIVATE-ORACLE" not in call["prompt"] for call in calls)
        assert all("--ignore-user-config" in call["argv"] for call in calls)
        return {
            "compiler_pipeline": "passed",
            "cli_stand_in": True,
            "model_calls": 0,
            "stages": len(manifest["stages"]),
            "admission": manifest["admission_status"],
            "private_expectations_excluded": True,
        }


if __name__ == "__main__":
    print(json.dumps(smoke(), indent=2))
