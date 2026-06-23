"""A2A (Agent-to-Agent) interface for inter-agent communication via Unix domain sockets.

Agents communicate through ``/tmp/taua2a-{PID}.sock`` using JSON messages.

Protocol:
- Agent card request (sync): ``{"type": "agent_card"}`` → agent metadata
- Query request (async): ``{"type": "query", "id": <uuid>, "query": <prompt>}``
  → ``{"type": "queued", "id": <request_id>}`` → ``{"type": "response", "id": <request_id>, "response": <result>}``

Key components:
- A2AServer: Handles incoming connections (agent_card, query) in a daemon thread
- connect_to_agent, get_agent_card, query_agent, list_agents: Client utilities
"""

import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

from agent_console import (
    a2a_cli_error, a2a_started_message, agent_a2a_response, agent_card_json,
    agent_status_message, agents_json, agents_table_header, agents_table_row,
)
from agent_models import InputMessage

__all__ = [
    "A2AServer",
    "connect_to_agent",
    "get_agent_card",
    "list_agents",
    "query_agent",
    "a2a_cli_mode",
]

# ── Constants ──────────────────────────────────────────────────────────────

DEFAULT_CONNECT_TIMEOUT = 5
DEFAULT_ACK_TIMEOUT = 5
DEFAULT_POLL_INTERVAL = 0.1
SOCKET_BUFFER = 4096

# Heartbeat protocol: server sends periodic heartbeats while processing.
# Client considers connection alive as long as heartbeats arrive within
# HEARTBEAT_IDLE_TIMEOUT seconds. No wall-clock timeout — slow agents are fine.
HEARTBEAT_INTERVAL = 5.0          # Server sends heartbeat every N seconds
HEARTBEAT_IDLE_TIMEOUT = 30.0     # Client gives up if no heartbeat for N seconds


# ── Client utilities ──────────────────────────────────────────────────────


def _verify_socket(sock_path: str) -> bool:
    """Check if a socket file exists and accepts connections."""
    if not Path(sock_path).exists():
        return False
    try:
        test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        test_sock.settimeout(1)
        test_sock.connect(sock_path)
        test_sock.close()
        return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


def _make_connection(sock_path: str, timeout: float) -> socket.socket:
    """Create and connect a Unix domain socket with the given timeout."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    client.connect(sock_path)
    return client


def connect_to_agent(pid: int, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> socket.socket:
    """Connect to an agent by PID via its Unix socket."""
    sock_path = f"/tmp/taua2a-{pid}.sock"

    if not Path(sock_path).exists():
        raise FileNotFoundError(f"Socket not found: {sock_path}")

    if not _verify_socket(sock_path):
        raise ConnectionError(f"Agent {pid} not responding (socket may be stale)")

    return _make_connection(sock_path, timeout)


def _decode_json_stream(client: socket.socket, timeout: float, initial_buffer: str = ""):
    """Decode one JSON object from a socket stream.

    Reads until a complete JSON object is available, then returns
    ``(obj, remainder_buffer)``.
    """
    client.settimeout(timeout)
    decoder = json.JSONDecoder()
    buffer = initial_buffer

    while True:
        try:
            chunk = client.recv(SOCKET_BUFFER).decode()
            if not chunk:
                break
            buffer += chunk
            while buffer:
                try:
                    obj, idx = decoder.raw_decode(buffer)
                    return obj, buffer[idx:]
                except json.JSONDecodeError:
                    break
        except TimeoutError as exc:
            raise TimeoutError("Response timed out") from exc
    return None, buffer


def _read_json_response(client: socket.socket, timeout: float = DEFAULT_ACK_TIMEOUT):
    """Read a complete JSON object from a socket, returning (obj, remainder_buffer)."""
    return _decode_json_stream(client, timeout)


def get_agent_card(client: socket.socket) -> dict:
    """Fetch the agent card from a connected socket (synchronous)."""
    client.send(json.dumps({"type": "agent_card"}).encode())
    data, _ = _read_json_response(client, DEFAULT_ACK_TIMEOUT)

    if data.get("type") == "agent_card":
        return data
    raise RuntimeError(f"Unexpected response: {data}")


def _wait_for_response(
    client: socket.socket,
    request_id: str,
    initial_buffer: str = "",
    idle_timeout: float = HEARTBEAT_IDLE_TIMEOUT,
) -> dict:
    """Wait for a response matching *request_id*, accepting heartbeats.

    Times out only if no data (response or heartbeat) arrives for *idle_timeout*
    seconds. As long as heartbeats keep flowing, the connection stays alive
    indefinitely — no wall-clock timeout.
    """
    decoder = json.JSONDecoder()
    last_activity = time.time()
    buffer = initial_buffer

    while True:
        try:
            client.settimeout(1.0)
            chunk = client.recv(SOCKET_BUFFER).decode()
            if not chunk:
                continue
            buffer += chunk
            last_activity = time.time()  # Any data resets idle timer
            while buffer:
                start_idx = buffer.find("{")
                if start_idx == -1:
                    break
                try:
                    obj, end_idx = decoder.raw_decode(buffer[start_idx:])
                    buffer = buffer[start_idx + end_idx :]

                    if obj.get("id") == request_id and obj.get("type") == "response":
                        return obj
                    # Heartbeats are accepted but not returned — they just keep us alive
                except json.JSONDecodeError:
                    break
        except TimeoutError:
            if time.time() - last_activity >= idle_timeout:
                raise TimeoutError(
                    f"Agent stopped responding (no heartbeat for {idle_timeout}s)"
                ) from None
            continue

    raise TimeoutError(f"Agent did not respond within {idle_timeout}s")


def query_agent(client: socket.socket, prompt: str, idle_timeout: float = HEARTBEAT_IDLE_TIMEOUT) -> dict:
    """Send a query to an agent and wait for the result (async protocol).

    No wall-clock timeout — waits indefinitely as long as the server sends
    heartbeats. Times out only if no heartbeat arrives for *idle_timeout* seconds.
    """
    request_id = str(uuid.uuid4())

    client.send(
        json.dumps(
            {
                "type": "query",
                "id": request_id,
                "query": prompt,
            }
        ).encode()
    )

    ack, ack_remainder = _read_json_response(client, DEFAULT_ACK_TIMEOUT)

    if ack.get("type") != "queued":
        raise RuntimeError(f"Unexpected acknowledgment: {ack}")

    return _wait_for_response(client, request_id, ack_remainder, idle_timeout)


def _get_agent_status(sock_path: str) -> dict | None:
    """Probe a socket and return an agent info dict (or None if PID unparseable)."""
    pid_match = Path(sock_path).stem.split("-")
    try:
        pid = int(pid_match[1])
    except (ValueError, IndexError):
        return None

    if not Path(sock_path).exists():
        return {"pid": pid, "sock_path": sock_path, "status": "stale"}

    if not _verify_socket(sock_path):
        return {"pid": pid, "sock_path": sock_path, "status": "unreachable"}

    try:
        client = _make_connection(sock_path, 2)
        card = get_agent_card(client)
        client.close()
    except (FileNotFoundError, ConnectionError, TimeoutError, RuntimeError, OSError):
        return {"pid": pid, "sock_path": sock_path, "status": "unreachable"}

    return {
        "pid": pid,
        "sock_path": sock_path,
        "name": card.get("name", "Unknown"),
        "tools_count": len(card.get("tools", [])),
        "working_dir": card.get("working_dir", "Unknown"),
        "model": card.get("model", "Unknown"),
        "file": card.get("file", "Unknown"),
        "status": "active",
    }


def list_agents() -> list:
    """Scan /tmp for active agent sockets and return their info dicts."""
    return [
        info
        for sock_file in Path("/tmp").glob("taua2a-*.sock")
        if (info := _get_agent_status(str(sock_file)))
    ]


# ── Server ─────────────────────────────────────────────────────────────────


class A2AServer:
    """Unix socket server for inter-agent communication.

    Runs in a daemon thread; handles ``agent_card`` (sync) and ``query`` (async)
    requests from other agents.
    """

    def __init__(self, agent, sock_path: str = None):
        """Initialize server for *agent*; defaults socket to ``/tmp/taua2a-{PID}.sock``."""
        self.agent = agent
        self.sock_path = sock_path or f"/tmp/taua2a-{os.getpid()}.sock"
        self.sock = None
        self.running = False
        self.thread = None
        self._ready = threading.Event()

    def start(self):
        """Start the server in a daemon thread; blocks until ready or 5s timeout."""
        self.running = True
        self._ready.clear()
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("A2A server failed to start within 5s")

    def stop(self):
        """Stop the server and clean up the socket file (idempotent)."""
        self.running = False

        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

        if hasattr(self, "sock_path") and Path(self.sock_path).exists():
            try:
                Path(self.sock_path).unlink(missing_ok=True)
            except OSError:
                pass

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

    def _accept_loop(self):
        """Bind socket, listen, and spawn a daemon thread per connection."""
        try:
            Path(self.sock_path).unlink(missing_ok=True)
        except OSError:
            pass

        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(self.sock_path)
        self.sock.listen(5)
        self._ready.set()
        self.sock.settimeout(1.0)

        while self.running:
            try:
                client_sock, _ = self.sock.accept()
                threading.Thread(
                    target=self._handle_client, args=(client_sock,), daemon=True
                ).start()
            except TimeoutError:
                continue
            except OSError:
                if self.running:
                    continue

    def _recv_request(self, client_sock: socket.socket) -> bytes:
        """Receive the full request from a client socket."""
        data = b""
        while True:
            chunk = client_sock.recv(SOCKET_BUFFER)
            if not chunk:
                break
            data += chunk
            if len(chunk) < SOCKET_BUFFER:
                break
        return data

    def _handle_client(self, client_sock: socket.socket):
        """Read a JSON request and dispatch to ``_send_agent_card`` or ``_handle_query``."""
        try:
            data = self._recv_request(client_sock)
            if not data:
                return

            request = json.loads(data.decode("utf-8"))
            request_id = request.get("id", str(uuid.uuid4()))
            request_type = request.get("type", "query")
            query_content = request.get("query", "")

            if request_type == "agent_card":
                self._send_agent_card(client_sock)
            else:
                self._handle_query(client_sock, request_id, query_content)
        except (OSError, RuntimeError, json.JSONDecodeError) as e:
            try:
                client_sock.send(
                    json.dumps({"type": "error", "message": str(e)}).encode() + b"\n"
                )
            except OSError:
                pass
        finally:
            client_sock.close()

    def _build_agent_card(self) -> dict:
        """Build the agent card dict."""
        return {
            "type": "agent_card",
            "name": self.agent.agent_name,
            "model": self.agent.model_name,
            "tools": sorted(self.agent.available_tool_names),
            "working_dir": str(self.agent.original_cwd),
            "context_length": len(self.agent.context),
            "uptime": (
                int(time.time() - self.agent._start_time)  # pylint: disable=W0212
                if hasattr(self.agent, "_start_time")
                else 0
            ),
            "sock_path": self.sock_path,
            "file": os.path.basename(sys.argv[0]) if sys.argv else "tau.py",
        }

    def _send_agent_card(self, client_sock: socket.socket):
        """Send agent metadata as JSON to *client_sock*."""
        try:
            client_sock.send(json.dumps(self._build_agent_card()).encode() + b"\n")
        except OSError:
            pass

    def _poll_for_response(self, client_sock: socket.socket, request_id: str) -> bool:
        """Poll for the response to *request_id* and send it to *client_sock*.

        Sends periodic heartbeats so the client knows the server is still alive.
        Polls indefinitely until response is available or client disconnects.

        Returns True if response was sent, False if client disconnected.
        """
        last_heartbeat = time.time()
        while True:
            try:
                if (
                    hasattr(self.agent, "_pending_a2a_responses")
                    and request_id in self.agent._pending_a2a_responses
                ):  # pylint: disable=W0212
                    result = self.agent._pending_a2a_responses.pop(
                        request_id
                    )  # pylint: disable=W0212
                    client_sock.send(json.dumps(result).encode() + b"\n")
                    return True

                # Send heartbeat if enough time has passed
                if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                    heartbeat = {"type": "heartbeat", "id": request_id}
                    client_sock.send(json.dumps(heartbeat).encode() + b"\n")
                    last_heartbeat = time.time()
            except OSError:
                return False
            time.sleep(DEFAULT_POLL_INTERVAL)
        return False

    def _handle_query(
        self, client_sock: socket.socket, request_id: str, query_content: str
    ):
        """Send ack, queue the query, then poll for the response with heartbeats."""
        ack = {"type": "queued", "id": request_id}
        try:
            client_sock.send(json.dumps(ack).encode() + b"\n")
        except OSError:
            return

        message = InputMessage.from_a2a(query_content, request_id)
        self.agent.input_queue.put(message)
        self._poll_for_response(client_sock, request_id)


# ── CLI helpers ────────────────────────────────────────────────────────────


def _filter_active_agents(agents: list) -> list:
    """Return only agents with status 'active'."""
    return [a for a in agents if a.get("status") == "active"]


def _empty_agents_message(include_all: bool) -> str:
    """Return the appropriate empty-agents message."""
    return "No agents found." if include_all else "No active agents found."


def _print_agents_table(agents: list, include_all: bool = False):
    """Display agents in a formatted table (active only unless *include_all*)."""
    if not include_all:
        agents = _filter_active_agents(agents)

    if not agents:
        agent_status_message(_empty_agents_message(include_all))
        return

    agents_table_header()
    for agent in agents:
        agents_table_row(agent)


def _print_agents_json(agents: list, include_all: bool = False):
    """Display agents as JSON."""
    if not include_all:
        agents = _filter_active_agents(agents)
    if not agents:
        agent_status_message(_empty_agents_message(include_all))
        return
    agents_json(json.dumps(agents, indent=2))


_A2A_CLI_ERRORS = (
    FileNotFoundError,
    ConnectionError,
    TimeoutError,
    RuntimeError,
)


def _cli_connect_and_execute(pid: int, action):
    """Connect to agent, run action(client), close, handle errors."""
    try:
        client = connect_to_agent(pid)
        action(client)
        client.close()
    except _A2A_CLI_ERRORS as e:
        a2a_cli_error(f"Error: {e}")
    sys.exit(0)


def _action_card(c):
    """Fetch and display the agent card."""
    agent_card_json(json.dumps(get_agent_card(c), indent=2))


def _handle_cli_card(pid: int):
    """Fetch and display the agent card for the specified PID, then exit."""
    _cli_connect_and_execute(pid, _action_card)


def _action_query(c, query: str, idle_timeout: float):
    """Send a query and display the response."""
    agent_a2a_response(query_agent(c, query, idle_timeout)["response"])


def _handle_cli_query(pid: int, query: str, idle_timeout: float):
    """Send a query to the specified agent, display the response, then exit."""
    _cli_connect_and_execute(pid, lambda c: _action_query(c, query, idle_timeout))


# ── List flag configurations ─────────────────────────────────────────────

_A2A_LIST_FLAGS = [
    ("list", False, False),
    ("list_all", False, True),
    ("listjson", True, False),
    ("listjson_all", True, True),
]


def a2a_cli_mode(args) -> None:
    """Handle A2A CLI mode: list agents, show card, or send query. Exits after."""
    # List mode (table or JSON, active or all)
    for list_flag, json_flag, include_all in _A2A_LIST_FLAGS:
        if getattr(args, list_flag):
            agents = list_agents()
            printer = _print_agents_json if json_flag else _print_agents_table
            printer(agents, include_all=include_all)
            sys.exit(0)

    # Card requires --pid
    if args.card and not args.pid:
        a2a_cli_error("Error: --card requires --pid")
        sys.exit(1)

    # Resolve target PID (by --pid or --name)
    target_pid = args.pid
    if args.name and not args.pid:
        agents = list_agents()
        found = next((a for a in agents if a.get("name") == args.name), None)
        if not found:
            a2a_cli_error(f"Error: Agent with name '{args.name}' not found.")
            sys.exit(1)
        target_pid = found["pid"]

    # Execute card or query
    if target_pid:
        query_value = args.inputs[0] if args.inputs else None

        if args.card:
            _handle_cli_card(target_pid)

        if query_value is not None:
            _handle_cli_query(target_pid, query_value, args.timeout)
