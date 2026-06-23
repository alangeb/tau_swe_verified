#!/usr/bin/env python3
"""signal_rpc.py — Signal CLI JSON-RPC helper."""
import json
import urllib.request

ENDPOINT = "http://localhost:8080/api/v1/rpc"

def rpc(method, params=None):
    """Send JSON-RPC request to signal-cli daemon."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": 1
    }).encode()
    req = urllib.request.Request(ENDPOINT, payload, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def send_message(phone, text):
    """Send message via JSON-RPC."""
    return rpc("send", {"message": text, "recipients": [phone]})

def receive_messages():
    """Receive pending messages."""
    return rpc("receive")

def list_accounts():
    """List registered accounts."""
    return rpc("listAccounts")

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "send":
        result = send_message(sys.argv[2], sys.argv[3])
        print(json.dumps(result, indent=2))
    elif sys.argv[1] == "receive":
        result = receive_messages()
        print(json.dumps(result, indent=2))
    elif sys.argv[1] == "accounts":
        result = list_accounts()
        print(json.dumps(result, indent=2))
    else:
        print("Usage: signal_rpc.py <send|receive|accounts> [args...]")
