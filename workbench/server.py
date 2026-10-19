# SPDX-License-Identifier: Apache-2.0
"""Loopback-only product workbench. python3 -m workbench.server"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs
import uuid

from expansion_lab.storage import PROJECT, Store, sha, write_json
from .engine import Watches
from . import demo
from .live import collect


class Service:
    def __init__(self):
        self.store=Store(); self.engine=Watches(PROJECT/"data/workbench/watches.sqlite3",self.store)
        self.token=secrets.token_urlsafe(32); self.jobs={}; self.pool=ThreadPoolExecutor(max_workers=1)

    def compile(self, task):
        if task.get("provider") not in ["github","stripe","slack"]: raise ValueError("Choose a supported provider")
        if not isinstance(task.get("task"),str) or not 5<=len(task["task"])<=8000: raise ValueError("Task must be 5–8000 characters")
        if not isinstance(task.get("context"),dict): raise ValueError("Context must be a JSON object")
        identity="task-"+uuid.uuid4().hex
        task=dict(task,id=identity)
        self.jobs[identity]={"id":identity,"status":"queued","task":task}
        directory=PROJECT/"data/tasks"/identity
        write_json(directory/"public.json",{"tasks":[task]})
        write_json(directory/"private.json",{"probes":{}})
        write_json(directory/"manifest.json",{name:sha((directory/name).read_bytes()) for name in ["public.json","private.json"]})
        def work():
            job=self.jobs[identity]; job["status"]="compiling"
            try:
                process=subprocess.run([sys.executable,"-m","workbench.compiler",identity],cwd=PROJECT,capture_output=True,text=True,timeout=1200)
                job["log"]=self.store.put(process.stdout+process.stderr,"text/plain")
                if process.returncode: raise ValueError("Compilation did not finish. Inspect the saved run before retrying.")
                manifest=json.loads((PROJECT/"data/runs"/identity/"manifest.json").read_text())
                plan=self.store.get(manifest["plans"])["plans"][0]
                from .engine import admit
                admit(plan,task)
                job.update(status="completed",plan=plan,run=identity)
            except Exception as exc: job.update(status="failed",error=str(exc))
        self.pool.submit(work); return self.jobs[identity]


class Handler(BaseHTTPRequestHandler):
    server_version="Thenwake/0.1"

    def log_message(self, format, *args): pass

    def respond(self, value, status=200):
        body=json.dumps(value,allow_nan=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(body))); self.send_header("Cache-Control","no-store"); self.send_header("X-Content-Type-Options","nosniff"); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        try:
            if not self.valid_host(): return self.respond({"error":"Invalid host"},403)
            path=urlsplit(self.path).path
            if path in ["/","/app.js","/style.css"]:
                file=Path(__file__).parent/"static"/({"/":"index.html","/app.js":"app.js","/style.css":"style.css"}[path])
                body=file.read_bytes(); self.send_response(200); self.send_header("Content-Type",{"/":"text/html; charset=utf-8","/app.js":"text/javascript; charset=utf-8","/style.css":"text/css; charset=utf-8"}[path]); self.send_header("Content-Length",str(len(body))); self.send_header("Cache-Control","no-store"); self.send_header("X-Content-Type-Options","nosniff"); self.send_header("Content-Security-Policy","default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"); self.end_headers(); self.wfile.write(body); return
            if path=="/api/bootstrap":
                audit_path=PROJECT/"data/runs/source-backed-regression-1/manifest.json"
                manifest=json.loads(audit_path.read_text()) if audit_path.exists() else {}
                audit=self.server.service.store.get(manifest["audit"]) if manifest.get("audit") else None
                evaluation={k:audit[k] for k in ["run_id","corrected","structural_issues"]} if audit else None
                supplements=PROJECT/"data/sources/supplements.json"
                return self.respond({"token":self.server.service.token,"templates":demo.catalog(),"evaluation":evaluation,"sources":json.loads(supplements.read_text()) if supplements.exists() else [],"mode":"local prototype; simulated replay unless live public GET is selected"})
            if path.startswith("/api/watches/"):
                parts=path.split("/"); identity=parts[3]
                if len(parts)==4: return self.respond(self.server.service.engine.get(identity))
                if parts[4]=="outbox":
                    query=parse_qs(urlsplit(self.path).query)
                    return self.respond(self.server.service.engine.outbox(identity,int(query.get("after",[0])[0]),query.get("recent")==["1"]))
            if path.startswith("/api/jobs/"):
                identity=path.rsplit("/",1)[-1]
                if identity not in self.server.service.jobs: raise KeyError("Job not found")
                return self.respond(self.server.service.jobs[identity])
            self.respond({"error":"Not found"},404)
        except KeyError as exc: self.respond({"error":str(exc)},404)
        except (ValueError,TypeError) as exc: self.respond({"error":str(exc)},400)
        except Exception: self.respond({"error":"Local service error; inspect terminal output"},500)

    def valid_host(self):
        return self.headers.get("Host") in [f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}"]

    def do_POST(self):
        try:
            service=self.server.service
            if not self.valid_host() or self.headers.get("X-Observation-Token")!=service.token: return self.respond({"error":"Invalid local session"},403)
            origin=self.headers.get("Origin")
            if origin and origin not in [f"http://127.0.0.1:{self.server.server_port}",f"http://localhost:{self.server.server_port}"]: return self.respond({"error":"Cross-origin request denied"},403)
            length=int(self.headers.get("Content-Length","0"))
            if not 0<length<=65536: return self.respond({"error":"JSON request must be at most 64 KiB"},413)
            body=json.loads(self.rfile.read(length)); path=urlsplit(self.path).path
            if not isinstance(body,dict): raise ValueError("Expected a JSON object")
            if path=="/api/compile": return self.respond(service.compile(body),202)
            if path=="/api/watches":
                if "template_id" in body:
                    template=next((t for t in demo.catalog() if t["task"]["id"]==body["template_id"]),None)
                    if template is None: raise ValueError("Unknown compiled task")
                    task=dict(template["task"],_origin_run=template["run"],_mode=template["mode"])
                    return self.respond(service.engine.create(task,template["plan"],template["phase"],body.get("ttl",30)),201)
                job=service.jobs.get(body.get("job_id"))
                if job is None or job["status"]!="completed": raise ValueError("Compile and validate a plan first")
                task=dict(job["task"],_origin_run=job["run"],_mode="caller-supplied snapshot")
                return self.respond(service.engine.create(task,job["plan"],body.get("phase","after"),body.get("ttl",30)),201)
            if path.startswith("/api/watches/"):
                parts=path.split("/"); identity=parts[3]; action=parts[4]
                watch=service.engine.get(identity)
                if action=="replay":
                    snapshots=demo.snapshots(watch["task"],watch["plan"],body.get("case","success"),watch["phase"])
                    result=service.engine.submit(identity,uuid.uuid4().hex,watch["revision"]+1,snapshots,time.time()-watch["ttl"]-1 if body.get("stale") else None,"simulated replay")
                    return self.respond(result)
                if action=="events": return self.respond(service.engine.submit(identity,body["event_id"],body["revision"],body["snapshots"],body.get("observed_at")))
                if action=="live":
                    if watch["task"]["provider"]!="github": raise ValueError("Live demo supports public GitHub GETs only")
                    snapshots,started=collect(watch["plan"],watch["task"]["context"],watch["phase"])
                    return self.respond(service.engine.submit(identity,uuid.uuid4().hex,watch["revision"]+1,snapshots,started,"live public GitHub GET"))
                if action=="ack": service.engine.ack(identity,body["id"]); return self.respond({"acknowledged":body["id"]})
            return self.respond({"error":"Not found"},404)
        except KeyError as exc: self.respond({"error":str(exc)},404)
        except (ValueError,TypeError,IndexError) as exc: self.respond({"error":str(exc)},400)
        except Exception as exc:
            print("Service error:",type(exc).__name__,str(exc),flush=True)
            self.respond({"error":"Local service error; inspect terminal output"},500)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--port",type=int,default=8765); args=parser.parse_args()
    server=ThreadingHTTPServer(("127.0.0.1",args.port),Handler); server.service=Service()
    stop=threading.Event()
    def sweeper():
        while not stop.wait(1):
            try: server.service.engine.expire()
            except Exception as exc: print("Expiry sweep failed:",type(exc).__name__,flush=True)
    threading.Thread(target=sweeper,daemon=True).start()
    print(f"Thenwake: http://127.0.0.1:{args.port}",flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: stop.set(); server.server_close(); server.service.pool.shutdown(wait=False,cancel_futures=True)


if __name__=="__main__": main()
