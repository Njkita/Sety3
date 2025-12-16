import asyncio
import os
import ssl

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response

from backends import Backend, BackendPool

app = FastAPI()

MESH_CERT_DIR = os.getenv("MESH_CERT_DIR", "/mesh/certs")
MESH_CA = os.path.join(MESH_CERT_DIR, "ca.crt")
MESH_CERT = os.path.join(MESH_CERT_DIR, "lb.crt")
MESH_KEY = os.path.join(MESH_CERT_DIR, "lb.key")
MESH_CLIENT_CERT = (MESH_CERT, MESH_KEY)


notes_http_backends = BackendPool(
    [
        Backend(name="svc1", url="https://service1:9443"),
        Backend(name="svc2", url="https://service2:9443"),
    ],
    verify_ca=MESH_CA,
    client_cert=MESH_CLIENT_CERT,
)

mailer_http_backends = BackendPool(
    [
        Backend(name="mailer", url="https://mailer:9443"),
    ],
    verify_ca=MESH_CA,
    client_cert=MESH_CLIENT_CERT,
)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(notes_http_backends.health_check_loop())
    asyncio.create_task(mailer_http_backends.health_check_loop())
    asyncio.create_task(start_grpc_lb())


def _pick_pool_by_path(full_path: str) -> BackendPool:
    if full_path == "mail" or full_path.startswith("mail/"):
        return mailer_http_backends
    return notes_http_backends

@app.get("/__debug/backends")
def debug_backends():
    def snap(pool: BackendPool):
        return [
            {
                "name": b.name,
                "url": b.url,
                "alive": b.alive,
                "failures": b.failures,
                "circuit_open_until": b.circuit_open_until,
            }
            for b in pool.backends
        ]
    return {"notes": snap(notes_http_backends), "mailer": snap(mailer_http_backends)}


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_all(full_path: str, request: Request):
    pool = _pick_pool_by_path(full_path)

    backend = pool.pick_backend()
    if not backend:
        return Response(status_code=503, content="No backend available")

    url = f"{backend.url}/{full_path}"
    method = request.method
    headers = dict(request.headers)
    body = await request.body()

    try:
        async with httpx.AsyncClient(
            timeout=2.0,
            verify=pool.ssl_ctx,
            trust_env=False,
        ) as client:
            r = await client.request(method, url, headers=headers, content=body)

    except Exception as e:
        backend.record_failure()
        return Response(status_code=502, content="Backend error")



    backend.record_success()
    return Response(
        status_code=r.status_code,
        content=r.content,
        headers={
            k: v
            for k, v in r.headers.items()
            if k.lower() not in ["content-length", "transfer-encoding", "connection"]
        },
    )


GRPC_BACKENDS = [("service1", 9444), ("service2", 9444)]


async def handle_grpc_client(reader, writer):
    backend = GRPC_BACKENDS[handle_grpc_client.counter % len(GRPC_BACKENDS)]
    handle_grpc_client.counter += 1
    host, port = backend

    client_ctx = ssl.create_default_context(cafile=MESH_CA)
    client_ctx.load_cert_chain(MESH_CERT, MESH_KEY)
    client_ctx.set_alpn_protocols(["h2"])

    try:
        backend_reader, backend_writer = await asyncio.open_connection(
            host, port, ssl=client_ctx, server_hostname=host
        )
    except Exception:
        writer.close()
        await writer.wait_closed()
        return

    async def pipe(src, dst):
        try:
            while True:
                data = await src.read(1024 * 16)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except Exception:
            pass
        finally:
            try:
                dst.close()
            except Exception:
                pass

    await asyncio.gather(
        pipe(reader, backend_writer),
        pipe(backend_reader, writer),
    )


handle_grpc_client.counter = 0


async def start_grpc_lb():
    certfile = os.path.join("certs", "lb.crt")
    keyfile = os.path.join("certs", "lb.key")
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(certfile, keyfile)
    ssl_ctx.set_alpn_protocols(["h2"])

    server = await asyncio.start_server(
        handle_grpc_client,
        host="0.0.0.0",
        port=8444,
        ssl=ssl_ctx,
    )
    print("[LB] gRPC LB listening on :8444 (TLS)")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8443,
        ssl_keyfile="certs/lb.key",
        ssl_certfile="certs/lb.crt",
    )
