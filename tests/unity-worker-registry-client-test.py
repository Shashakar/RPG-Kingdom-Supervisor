#!/usr/bin/env python3
"""Controller socket client round trip and fail-closed response tests."""
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
from threading import Thread

root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("client",root/"scripts"/"unity-worker-registry-client.py")
client=importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
with tempfile.TemporaryDirectory() as temp:
    path=str(Path(temp)/"registrar.sock")
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
        listener.bind(path)
        listener.listen(1)
        def serve_reply(reply):
            connection,_=listener.accept()
            with connection:
                request=bytearray()
                while b"\n" not in request:
                    request.extend(connection.recv(4096))
                assert json.loads(request.split(b"\n",1)[0])["action"]=="register"
                connection.sendall(json.dumps(reply).encode()+b"\n")
        thread=Thread(target=serve_reply,args=({"ok":True,"registration":{"pid":321}},))
        thread.start()
        value=client.submit(path,{"action":"register"})
        thread.join()
        assert value["registration"]["pid"]==321
        thread=Thread(target=serve_reply,args=({"ok":False,"error":"PermissionError"},))
        thread.start()
        try:
            client.submit(path,{"action":"register"})
        except PermissionError:
            pass
        else:
            raise AssertionError("rejected registration accepted")
        thread.join()
print("unity-worker-registry-client-test: PASS")
