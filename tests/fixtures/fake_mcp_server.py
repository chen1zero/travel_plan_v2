"""Minimal line-delimited JSON-RPC server used by MCP client tests."""

import json
import os
import sys


def respond(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")

    if method == "initialize":
        respond(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "fake-amap",
                        "version": "1.0",
                    },
                },
            }
        )
    elif method == "tools/list":
        respond(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "tools": [
                        {
                            "name": "maps_weather",
                            "description": "查询城市天气",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "city": {"type": "string"}
                                },
                                "required": ["city"],
                            },
                        },
                        {
                            "name": "maps_text_search",
                            "description": "搜索 POI",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "keywords": {"type": "string"}
                                },
                                "required": ["keywords"],
                            },
                        },
                    ]
                },
            }
        )
    elif method == "tools/call":
        tool_name = message["params"]["name"]
        arguments = message["params"]["arguments"]
        if tool_name == "maps_weather":
            result = {
                "city": arguments["city"],
                "forecasts": [{"date": "2026-07-31"}],
                "api_key_configured": bool(
                    os.getenv("AMAP_MAPS_API_KEY")
                ),
                "server_pid": os.getpid(),
            }
        else:
            result = {
                "pois": [
                    {
                        "name": "历史博物馆",
                        "address": "测试路1号",
                        "typecode": "140100",
                    }
                ],
                "keywords": arguments["keywords"],
            }
        respond(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                result,
                                ensure_ascii=False,
                            ),
                        }
                    ],
                    "isError": False,
                },
            }
        )
