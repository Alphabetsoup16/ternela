# SPDX-License-Identifier: Apache-2.0
"""Tiny standard-library client for the local prototype API."""
import json
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


class ObservationClient:
    def __init__(self, base_url="http://127.0.0.1:8765"):
        origin=urlsplit(base_url)
        if origin.scheme!="http" or origin.hostname not in ["127.0.0.1","localhost"]: raise ValueError("Prototype client supports loopback only")
        self.base=base_url.rstrip("/"); self.token=self._request("/api/bootstrap")["token"]

    def _request(self,path,body=None):
        data=json.dumps(body,allow_nan=False).encode() if body is not None else None
        headers={"Content-Type":"application/json","X-Observation-Token":getattr(self,"token","")}
        with urlopen(Request(self.base+path,data=data,headers=headers),timeout=90) as response: return json.load(response)

    def watch(self,template_id,ttl=30): return self._request("/api/watches",{"template_id":template_id,"ttl":ttl})
    def submit(self,watch_id,event_id,revision,snapshots,observed_at=None):
        return self._request(f"/api/watches/{watch_id}/events",dict(event_id=event_id,revision=revision,snapshots=snapshots,observed_at=observed_at))
    def get(self,watch_id): return self._request(f"/api/watches/{watch_id}")
    def wakeups(self,watch_id,after=0): return self._request(f"/api/watches/{watch_id}/outbox?after={int(after)}")
    def acknowledge(self,watch_id,message_id): return self._request(f"/api/watches/{watch_id}/ack",{"id":message_id})
    def compile(self,task,provider,context): return self._request("/api/compile",dict(task=task,provider=provider,context=context))
    def job(self,job_id): return self._request(f"/api/jobs/{job_id}")
    def watch_compiled(self,job_id,phase="after",ttl=30): return self._request("/api/watches",dict(job_id=job_id,phase=phase,ttl=ttl))
