import asyncio
import ssl
import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import httpx


@dataclass
class Backend:
    name: str
    url: str  # https://service:9443
    failures: int = 0
    last_failure: float = 0.0
    circuit_open_until: float = 0.0
    alive: bool = True

    def is_available(self) -> bool:
        now = time.time()
        if self.circuit_open_until > now:
            return False
        return self.alive

    def record_failure(self):
        now = time.time()
        self.failures += 1
        self.last_failure = now
        self.alive = False
        if self.failures >= 3:
            self.circuit_open_until = now + 10

    def record_success(self):
        self.failures = 0
        self.alive = True
        self.circuit_open_until = 0.0


class BackendPool:
    def __init__(self, backends, verify_ca, client_cert):
        self.backends = backends
        self._rr_index = 0
        self.verify_ca = verify_ca
        self.client_cert = client_cert

        self.ssl_ctx = ssl.create_default_context(cafile=self.verify_ca)
        self.ssl_ctx.load_cert_chain(self.client_cert[0], self.client_cert[1])

    def pick_backend(self) -> Optional[Backend]:
        alive = [b for b in self.backends if b.is_available()]
        if not alive:
            return None
        b = alive[self._rr_index % len(alive)]
        self._rr_index += 1
        return b

    async def health_check_loop(self):
        async with httpx.AsyncClient(
            timeout=2.0,
            verify=self.ssl_ctx,
            trust_env=False,
        ) as client:
            while True:
                for b in self.backends:
                    try:
                        r = await client.get(f"{b.url}/health")
                        if r.status_code == 200:
                            b.record_success()
                        else:
                            b.record_failure()
                    except Exception as e:
                        print(f"[HC] {b.name} {b.url} ERR {type(e).__name__}: {e!r}", flush=True)
                        b.record_failure()
                await asyncio.sleep(2.0)

