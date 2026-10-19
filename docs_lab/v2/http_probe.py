# SPDX-License-Identifier: Apache-2.0
"""Independent read-only HTTP pagination integration probe, not a generated-plan win."""

import json
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import quote, urlencode, urlsplit

from .runtime import execute


def transport(call):
    if not call["operation"].startswith("GET "):
        raise ValueError("This public integration transport permits GET only")
    bindings=call["bindings"]
    path=call["operation"].split(" ",1)[1]
    for name,value in bindings.get("path",{}).items():path=path.replace("{"+name+"}",quote(str(value),safe=""))
    query=urlencode(bindings.get("query",{}))
    url=call.get("next_url") or "https://api.github.com"+path+("?"+query if query else "")
    parsed=urlsplit(url)
    if parsed.scheme!="https" or parsed.netloc!="api.github.com":raise ValueError("Unexpected public API origin")
    if not (parsed.path.startswith("/repos/cli/cli/") or parsed.path.startswith("/repositories/")):
        raise ValueError("Outside public repository probe scope")
    with tempfile.TemporaryDirectory(prefix="public-pagination-") as work:
        headers=Path(work)/"headers";body=Path(work)/"body"
        proc=subprocess.run(["/usr/bin/curl","--silent","--show-error","--max-time","30","--dump-header",str(headers),"--output",str(body),
                             "--write-out","%{http_code}","--user-agent","observation-docs-lab/1.0",url],capture_output=True,text=True)
        if proc.returncode:return {"http_status":0,"body":None,"headers_complete":False,"url":url}
        lines=headers.read_text().replace("\r\n","\n").splitlines();captured={}
        for line in lines:
            if line.startswith("HTTP/"):captured={}
            elif ":" in line:
                name,value=line.split(":",1);captured[name.lower()]=value.strip()
        return {"http_status":int(proc.stdout),"body":json.loads(body.read_text()),"headers":captured,"headers_complete":True,"url":url}


def main():
    request={"operation":"GET /repos/{owner}/{repo}/actions/runs","bindings_json":json.dumps({"path":{"owner":"cli","repo":"cli"},"query":{"per_page":1}}),
             "pagination":{"mode":"link","items_pointer":"/workflow_runs","cursor_parameter":"","next_cursor_pointer":"","has_more_pointer":"","cursor_item_pointer":"","max_pages":2}}
    result=execute(request,{},transport)
    destination=Path("results/docs/v2-http-pagination.json")
    output={"planner_generated":False,"purpose":"Independent live transport check; not counted in development or fresh task scores",
            "expected":"Two public GET pages, incomplete because more pages exist", "result":result,
            "passed":len(result["trace"])==2 and result["collection_complete"] is False and len(result.get("body",{}).get("workflow_runs",[]))==2}
    destination.write_text(json.dumps(output,indent=2)+"\n")
    print(json.dumps({"pages":len(result["trace"]),"complete":result["collection_complete"],"reason":result["traversal_reason"],"passed":output["passed"]}))


if __name__=="__main__":main()
