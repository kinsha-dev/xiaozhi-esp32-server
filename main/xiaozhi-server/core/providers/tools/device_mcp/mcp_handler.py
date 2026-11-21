"""Device-side MCP client support module"""

import json
import asyncio
import re
from core.utils.util import get_vision_url
from core.utils.auth import AuthToken
from config.logger import setup_logging
from .mcp_client import MCPClient
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

async def send_mcp_message(conn: "ConnectionHandler", payload: dict):
    """Helper to send MCP messages, encapsulating common logic."""
    if not conn.features.get("mcp"):
        logger.bind(tag=TAG).warning("Client does not support MCP, cannot send the MCP message")
        return

    message = json.dumps({"type": "mcp", "payload": payload})

    try:
        await conn.websocket.send(message)
        logger.bind(tag=TAG).debug(f"MCP message sent: {message}")
    except Exception as e:
        logger.bind(tag=TAG).error(f"Failed to send MCP message: {e}")


async def handle_mcp_message(
    conn: "ConnectionHandler", mcp_client: MCPClient, payload: dict
):
    """Handle MCP messages, including initialization, tool lists and tool call responses"""
    logger.bind(tag=TAG).debug(f"Handling MCP message: {str(payload)[:100]}")

    if not isinstance(payload, dict):
        logger.bind(tag=TAG).error("MCP message is missing the payload field or is malformed")
        return

    # Handle result
    if "result" in payload:
        result = payload["result"]
        msg_id = int(payload.get("id", 0))

        # Check for tool call response first
        if msg_id in mcp_client.call_results:
            logger.bind(tag=TAG).debug(
                f"Received tool call response, ID: {msg_id}, result: {result}"
            )
            await mcp_client.resolve_call_result(msg_id, result)
            return

        if msg_id == 1:  # mcpInitializeID
            logger.bind(tag=TAG).debug("Received MCP initialize response")
            server_info = result.get("serverInfo")
            if isinstance(server_info, dict):
                name = server_info.get("name")
                version = server_info.get("version")
                logger.bind(tag=TAG).debug(
                    f"Client MCP server info: name={name}, version={version}"
                )
            return

        elif msg_id == 2:  # mcpToolsListID
            logger.bind(tag=TAG).debug("Received MCP tool list response")
            if isinstance(result, dict) and "tools" in result:
                tools_data = result["tools"]
                if not isinstance(tools_data, list):
                    logger.bind(tag=TAG).error("Malformed tool list")
                    return

                logger.bind(tag=TAG).info(
                    f"Number of tools supported by the client device: {len(tools_data)}"
                )

                for i, tool in enumerate(tools_data):
                    if not isinstance(tool, dict):
                        continue

                    name = tool.get("name", "")
                    description = tool.get("description", "")
                    input_schema = {"type": "object", "properties": {}, "required": []}

                    if "inputSchema" in tool and isinstance(tool["inputSchema"], dict):
                        schema = tool["inputSchema"]
                        input_schema["type"] = schema.get("type", "object")
                        input_schema["properties"] = schema.get("properties", {})
                        input_schema["required"] = [
                            s for s in schema.get("required", []) if isinstance(s, str)
                        ]

                    new_tool = {
                        "name": name,
                        "description": description,
                        "inputSchema": input_schema,
                    }
                    await mcp_client.add_tool(new_tool)
                    logger.bind(tag=TAG).debug(f"Client tool #{i+1}: {name}")

                # replace tool names in all tool descriptions
                for tool_data in mcp_client.tools.values():
                    if "description" in tool_data:
                        description = tool_data["description"]
                        # iterate over all tool names to replace
                        for (
                            sanitized_name,
                            original_name,
                        ) in mcp_client.name_mapping.items():
                            description = description.replace(
                                original_name, sanitized_name
                            )
                        tool_data["description"] = description

                next_cursor = result.get("nextCursor", "")
                if next_cursor:
                    logger.bind(tag=TAG).debug(f"There are more tools, nextCursor: {next_cursor}")
                    await send_mcp_tools_list_continue_request(conn, next_cursor)
                else:
                    await mcp_client.set_ready(True)
                    logger.bind(tag=TAG).debug("All tools fetched, MCP client ready")

                    # refresh the tool cache so MCP tools are included in the function list
                    if hasattr(conn, "func_handler") and conn.func_handler:
                        conn.func_handler.tool_manager.refresh_tools()
                        conn.func_handler.current_support_functions()
            return

    # Handle method calls (requests from the client)
    elif "method" in payload:
        method = payload["method"]
        logger.bind(tag=TAG).info(f"Received MCP client request: {method}")

    elif "error" in payload:
        error_data = payload["error"]
        error_msg = error_data.get("message", "Unknown error")
        logger.bind(tag=TAG).error(f"Received MCP error response: {error_msg}")

        msg_id = int(payload.get("id", 0))
        if msg_id in mcp_client.call_results:
            await mcp_client.reject_call_result(
                msg_id, Exception(f"MCP error: {error_msg}")
            )


async def send_mcp_initialize_message(conn: "ConnectionHandler"):
    """Send the MCP initialize message"""

    vision_url = get_vision_url(conn.config)

    # generate the token from the secret key
    auth = AuthToken(conn.config["server"]["auth_key"])
    token = auth.generate_token(conn.headers.get("device-id"))

    vision = {
        "url": vision_url,
        "token": token,
    }

    payload = {
        "jsonrpc": "2.0",
        "id": 1,  # mcpInitializeID
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {},
                "vision": vision,
            },
            "clientInfo": {
                "name": "XiaozhiClient",
                "version": "1.0.0",
            },
        },
    }
    logger.bind(tag=TAG).debug("Sending MCP initialize message")
    await send_mcp_message(conn, payload)


async def send_mcp_tools_list_request(conn: "ConnectionHandler"):
    """Send an MCP tool list request"""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,  # mcpToolsListID
        "method": "tools/list",
    }
    logger.bind(tag=TAG).debug("Sending MCP tool list request")
    await send_mcp_message(conn, payload)


async def send_mcp_tools_list_continue_request(conn: "ConnectionHandler", cursor: str):
    """Send an MCP tool list request with a cursor"""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,  # mcpToolsListID (same ID for continuation)
        "method": "tools/list",
        "params": {"cursor": cursor},
    }
    logger.bind(tag=TAG).info(f"Sending MCP tool list request with cursor: {cursor}")
    await send_mcp_message(conn, payload)


async def call_mcp_tool(
    conn: "ConnectionHandler",
    mcp_client: MCPClient,
    tool_name: str,
    args: str = "{}",
    timeout: int = 30,
):
    """
    Call the given tool and wait for the response
    """
    if not await mcp_client.is_ready():
        raise RuntimeError("MCP client is not ready yet")

    if not mcp_client.has_tool(tool_name):
        raise ValueError(f"Tool {tool_name} does not exist")

    tool_call_id = await mcp_client.get_next_id()
    result_future = asyncio.Future()
    await mcp_client.register_call_result_future(tool_call_id, result_future)

    # process the arguments
    try:
        if isinstance(args, str):
            # make sure the string is valid JSON
            if not args.strip():
                arguments = {}
            else:
                try:
                    # try to parse directly
                    arguments = json.loads(args)
                except json.JSONDecodeError:
                    # if parsing fails, try merging several JSON objects
                    try:
                        # use a regular expression to match all JSON objects
                        json_objects = re.findall(r"\{[^{}]*\}", args)
                        if len(json_objects) > 1:
                            # merge all JSON objects
                            merged_dict = {}
                            for json_str in json_objects:
                                try:
                                    obj = json.loads(json_str)
                                    if isinstance(obj, dict):
                                        merged_dict.update(obj)
                                except json.JSONDecodeError:
                                    continue
                            if merged_dict:
                                arguments = merged_dict
                            else:
                                raise ValueError(f"Unable to parse any valid JSON object: {args}")
                        else:
                            raise ValueError(f"Failed to parse arguments JSON: {args}")
                    except Exception as e:
                        logger.bind(tag=TAG).error(
                            f"Failed to parse arguments JSON: {str(e)}, original arguments: {args}"
                        )
                        raise ValueError(f"Failed to parse arguments JSON: {str(e)}")
        elif isinstance(args, dict):
            arguments = args
        else:
            raise ValueError(f"Wrong argument type, expected string or dict, got: {type(args)}")

        # make sure the arguments are a dict
        if not isinstance(arguments, dict):
            raise ValueError(f"Arguments must be a dict, got: {type(arguments)}")

    except Exception as e:
        if not isinstance(e, ValueError):
            raise ValueError(f"Failed to process arguments: {str(e)}")
        raise e

    actual_name = mcp_client.name_mapping.get(tool_name, tool_name)
    payload = {
        "jsonrpc": "2.0",
        "id": tool_call_id,
        "method": "tools/call",
        "params": {"name": actual_name, "arguments": arguments},
    }

    logger.bind(tag=TAG).info(f"Sending client MCP tool call request: {actual_name}, arguments: {args}")
    await send_mcp_message(conn, payload)

    try:
        # Wait for response or timeout
        raw_result = await asyncio.wait_for(result_future, timeout=timeout)
        logger.bind(tag=TAG).info(
            f"Client MCP tool call {actual_name} succeeded, raw result: {raw_result}"
        )

        if isinstance(raw_result, dict):
            if raw_result.get("isError") is True:
                error_msg = raw_result.get(
                    "error", "Tool call returned an error but gave no error details"
                )
                raise RuntimeError(f"Tool call error: {error_msg}")

            content = raw_result.get("content")
            if isinstance(content, list) and len(content) > 0:
                if isinstance(content[0], dict) and "text" in content[0]:
                    # return the text content directly without JSON parsing
                    return content[0]["text"]
        # if the result is not in the expected format, convert it to a string
        return str(raw_result)
    except asyncio.TimeoutError:
        await mcp_client.cleanup_call_result(tool_call_id)
        raise TimeoutError("Tool call request timed out")
    except Exception as e:
        await mcp_client.cleanup_call_result(tool_call_id)
        raise e
