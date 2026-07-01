# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Lightweight IPC channel between buda_viz and def_viz.

Both tools call connect_or_serve() at startup. The first one to start
binds as the server; the second connects as a client. If neither peer is
up yet, poll() retries connection on each timer tick.

Socket path: /tmp/buda_ipc_{session_name}.sock
Session name defaults to the stem of the design file so matching stems
auto-connect; use --ipc <name> to override when stems differ.

Message format: newline-delimited JSON.

buda_viz → def_viz:
  {"type": "select_bundle", "bundle_id": N, "net_names": [...], "inst_names": [...]}
  {"type": "clear"}

def_viz → buda_viz:
  {"type": "select_inst", "inst_names": [...]}
  {"type": "clear"}
"""

import collections
import json
import os
import socket
import threading
from typing import Callable, Optional

POLL_MS = 80
_SOCK_TEMPLATE = '/tmp/buda_ipc_{}.sock'


class VizIPC:
    def __init__(self, session_name: str, verbose: bool = True):
        self._path       = _SOCK_TEMPLATE.format(session_name)
        self._sock: Optional[socket.socket] = None   # connected peer socket
        self._srv:  Optional[socket.socket] = None   # listening socket (server role)
        self._connected  = False
        self._lock       = threading.Lock()           # guards _sock / _connected writes
        self._queue: collections.deque = collections.deque()
        self._recv_buf   = b''
        self.on_message: Optional[Callable[[dict], None]] = None
        # When False, suppress the informational connect/listen chatter (socket
        # errors are always printed).  buda_viz sets this from --ipc-verbose.
        self.verbose     = verbose

    # ── public API ────────────────────────────────────────────────────────────

    def connect_or_serve(self):
        """Try to connect as client; if that fails, bind as server."""
        if self._connected:
            return
        if self._srv is not None:
            # Already bound as server — only accept, never try to connect elsewhere.
            self._try_accept_nonblocking()
            return
        if self._try_connect():
            return
        self._try_serve()

    def send(self, msg: dict):
        """Serialize msg to a JSON line and send. No-op if not connected."""
        with self._lock:
            if not self._connected or self._sock is None:
                return
            try:
                data = (json.dumps(msg) + '\n').encode()
                self._sock.sendall(data)
            except OSError:
                self._disconnect()

    def poll(self):
        """
        Drain the incoming message queue and call on_message for each item.
        Also retries connection if currently disconnected.
        Call this from the GUI thread at POLL_MS intervals.
        """
        while self._queue:
            try:
                msg = self._queue.popleft()
            except IndexError:
                break
            if self.on_message is not None:
                try:
                    self.on_message(msg)
                except Exception as exc:
                    print(f'[viz_ipc] on_message error: {exc}')

        if not self._connected:
            self.connect_or_serve()

    def close(self):
        with self._lock:
            self._disconnect()
            if self._srv is not None:
                try:
                    self._srv.close()
                except OSError:
                    pass
                try:
                    os.unlink(self._path)
                except OSError:
                    pass
                self._srv = None

    # ── internal ──────────────────────────────────────────────────────────────

    def _try_connect(self) -> bool:
        """Return True if connection to existing server succeeded."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(self._path)
            with self._lock:
                self._sock = s
                self._connected = True
            self._start_recv_thread(s)
            if self.verbose:
                print(f'[viz_ipc] connected to {self._path}')
            return True
        except OSError:
            s.close()
            return False

    def _try_serve(self):
        """Bind as server. Cleans up stale socket file if present."""
        if self._srv is not None:
            # Already listening; just wait for a client.
            self._try_accept_nonblocking()
            return
        # Clean up stale socket (previous process crashed without cleanup).
        if os.path.exists(self._path):
            if not self._try_connect():   # double-check it's really stale
                if self.verbose:
                    print(f'[viz_ipc] unlinking stale {self._path}')
                try:
                    os.unlink(self._path)
                except OSError:
                    pass
            else:
                return  # it was live; we just connected
        try:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(self._path)
            srv.listen(1)
            srv.setblocking(False)
            self._srv = srv
            if self.verbose:
                print(f'[viz_ipc] listening on {self._path}')
        except OSError as exc:
            print(f'[viz_ipc] could not bind server: {exc}')

    def _try_accept_nonblocking(self):
        """Accept one pending connection if available."""
        if self._srv is None or self._connected:
            return
        try:
            conn, _ = self._srv.accept()
            conn.setblocking(True)   # macOS inherits O_NONBLOCK from server socket
            with self._lock:
                self._sock = conn
                self._connected = True
            self._start_recv_thread(conn)
            if self.verbose:
                print(f'[viz_ipc] accepted connection on {self._path}')
        except BlockingIOError:
            pass
        except OSError as exc:
            print(f'[viz_ipc] accept error: {exc}')

    def _start_recv_thread(self, sock: socket.socket):
        t = threading.Thread(target=self._recv_loop, args=(sock,), daemon=True)
        t.start()

    def _recv_loop(self, sock: socket.socket):
        buf = b''
        while True:
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    self._queue.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        self._disconnect()

    def _disconnect(self):
        """Must be called with _lock held or from recv thread (which owns the sock)."""
        self._connected = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        # Re-enable accept loop if we were the server so a new peer can connect.
        if self._srv is not None:
            self._srv.setblocking(False)
