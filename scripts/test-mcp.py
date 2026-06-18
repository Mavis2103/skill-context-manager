#!/usr/bin/env python3
"""Test MCP server with proper handshake sequence."""
import json
import subprocess
import sys
import time

server_cmd = [sys.executable, "-m", "scm.mcp_server"]

proc = subprocess.Popen(
    server_cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    cwd="/home/mavis/Workspaces/skill-context-manager",
    text=True,
)

def send(msg):
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line)
    proc.stdin.flush()
    # Read one response
    result = proc.stdout.readline().strip()
    return json.loads(result) if result else None

try:
    # Step 1: Initialize
    r1 = send({"jsonrpc":"2.0","id":1,"method":"initialize",
               "params":{"protocolVersion":"2024-11-05","capabilities":{},
                         "clientInfo":{"name":"test","version":"1.0"}}})
    print(f"1️⃣ Initialize: {r1['result']['serverInfo']['name']} v{r1['result']['serverInfo']['version']}")
    
    # Step 2: Initialized notification
    send({"jsonrpc":"2.0","method":"notifications/initialized"})
    
    # Step 3: List tools
    r2 = send({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
    tools = r2.get("result", {}).get("tools", [])
    print(f"2️⃣ Tools: {len(tools)} tools registered")
    for t in sorted(tools, key=lambda x: x["name"]):
        desc = t.get("description", "")[:60]
        print(f"   📌 {t['name']} — {desc}...")
    
    # Step 4: Call skill_query
    r3 = send({
        "jsonrpc":"2.0","id":3,"method":"tools/call",
        "params":{
            "name":"skill_query",
            "arguments":{"query":"deploy kubernetes", "top_k":2}
        }
    })
    result = json.loads(r3["result"]["content"][0]["text"])
    print(f"\n3️⃣ Query result: {len(result['results'])} skills")
    for s in result["results"]:
        print(f"   → {s['name']} ({s['score']})")
    
    # Step 5: Session start
    r4 = send({
        "jsonrpc":"2.0","id":4,"method":"tools/call",
        "params":{
            "name":"skill_session_start",
            "arguments":{"session_id":"mcp-test-full"}
        }
    })
    sess = json.loads(r4["result"]["content"][0]["text"])
    print(f"4️⃣ Session: {sess['status']} ({sess['session_id']})")
    
    # Step 6: Use skill
    r5 = send({
        "jsonrpc":"2.0","id":5,"method":"tools/call",
        "params":{
            "name":"skill_session_use",
            "arguments":{
                "session_id":"mcp-test-full",
                "skill_name": result["results"][0]["name"],
                "query":"deploy kubernetes",
                "success": True
            }
        }
    })
    used = json.loads(r5["result"]["content"][0]["text"])
    print(f"5️⃣ Used: {used['skill']} → recent: {used['recent_skills']}")
    
    print(f"\n✅ All MCP protocol steps passed!")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    proc.terminate()
