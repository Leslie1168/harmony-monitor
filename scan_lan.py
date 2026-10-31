import socket, sys, concurrent.futures as cf

PORTS = [8710, 5555, 43891]
def probe(ip, port, t=0.35):
    s = socket.socket(); s.settimeout(t)
    try:
        s.connect((ip, port)); s.close(); return (ip, port)
    except Exception:
        return None
    finally:
        s.close()

SUBNET = sys.argv[1] if len(sys.argv) > 1 else "192.168.1"
tasks = [(f"{SUBNET}.{i}", p) for i in range(1, 255) for p in PORTS]
hits = []
with cf.ThreadPoolExecutor(max_workers=300) as ex:
    for r in ex.map(lambda a: probe(*a), tasks):
        if r: hits.append(r); print("OPEN", r[0], r[1], flush=True)
print("扫描完成，开放端口数:", len(hits))
