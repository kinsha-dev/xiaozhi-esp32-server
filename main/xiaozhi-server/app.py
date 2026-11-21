import sys
import uuid
import signal
import asyncio
from aioconsole import ainput
from config.settings import load_config
from config.logger import setup_logging
from core.utils.util import get_local_ip, validate_mcp_endpoint
from core.http_server import SimpleHttpServer
from core.websocket_server import WebSocketServer
from core.utils.util import check_ffmpeg_installed
from core.utils.gc_manager import get_gc_manager
from core.handle.helloHandle import preheat_wakeup_audio_cache

TAG = __name__
logger = setup_logging()


async def wait_for_exit() -> None:
    """
    Block until Ctrl-C / SIGTERM is received.
    - Unix: use add_signal_handler
    - Windows: rely on KeyboardInterrupt
    """
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    if sys.platform != "win32":  # Unix / macOS
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
    else:
        # Windows: await a future that never completes,
        # letting KeyboardInterrupt bubble up to asyncio.run, which avoids leftover plain threads blocking process exit
        try:
            await asyncio.Future()
        except KeyboardInterrupt:  # Ctrl‑C
            pass


async def monitor_stdin():
    """Watch standard input and consume the Enter key"""
    while True:
        await ainput()  # wait for input asynchronously and consume Enter


async def main():
    check_ffmpeg_installed()
    config = await load_config()

    # auth_key priority: config file server.auth_key > auto-generated
    # auth_key is used for jwt authentication, e.g. jwt auth of the vision analysis endpoint, ota endpoint token generation and websocket authentication
    # get the auth_key from the configuration file
    auth_key = config["server"].get("auth_key", "")
    
    # validate the auth_key; if invalid, generate a random key
    if not auth_key or len(auth_key) == 0 or "your" in auth_key.lower():
        auth_key = str(uuid.uuid4().hex)
    
    config["server"]["auth_key"] = auth_key

    # add the stdin monitoring task
    stdin_task = asyncio.create_task(monitor_stdin())

    # pre-warm the wake-word audio encoding cache so the first connection hits it
    asyncio.create_task(preheat_wakeup_audio_cache())

    # start the global GC manager (cleans up every 5 minutes)
    gc_manager = get_gc_manager(interval_seconds=300)
    await gc_manager.start()

    # start the WebSocket server
    ws_server = WebSocketServer(config)
    ws_task = asyncio.create_task(ws_server.start())
    # start the simple http server
    ota_server = SimpleHttpServer(config)
    ota_task = asyncio.create_task(ota_server.start())

    read_config_from_api = config.get("read_config_from_api", False)
    port = int(config["server"].get("http_port", 8003))
    if not read_config_from_api:
        logger.bind(tag=TAG).info(
            "OTA endpoint is\t\thttp://{}:{}/xiaozhi/ota/",
            get_local_ip(),
            port,
        )
    logger.bind(tag=TAG).info(
        "Vision analysis endpoint is\thttp://{}:{}/mcp/vision/explain",
        get_local_ip(),
        port,
    )
    mcp_endpoint = config.get("mcp_endpoint", None)
    if mcp_endpoint is not None and "your" not in mcp_endpoint.lower():
        # validate the MCP endpoint format
        if validate_mcp_endpoint(mcp_endpoint):
            logger.bind(tag=TAG).info("MCP endpoint is\t{}", mcp_endpoint)
            # convert the MCP endpoint address into a call address
            mcp_endpoint = mcp_endpoint.replace("/mcp/", "/call/")
            config["mcp_endpoint"] = mcp_endpoint
        else:
            logger.bind(tag=TAG).error("MCP endpoint is not valid")
            config["mcp_endpoint"] = "your MCP endpoint websocket address"

    # get the WebSocket configuration, with safe defaults
    websocket_port = 8000
    server_config = config.get("server", {})
    if isinstance(server_config, dict):
        websocket_port = int(server_config.get("port", 8000))

    logger.bind(tag=TAG).info(
        "Websocket address is\tws://{}:{}/xiaozhi/v1/",
        get_local_ip(),
        websocket_port,
    )

    logger.bind(tag=TAG).info(
        "======= The address above is a websocket protocol address, do not open it in a browser ======="
    )
    logger.bind(tag=TAG).info(
        "To test websocket, start the digital-human module and interact in a browser"
    )
    logger.bind(tag=TAG).info(
        "=============================================================\n"
    )

    try:
        await wait_for_exit()  # block until an exit signal is received
    except asyncio.CancelledError:
        print("Task cancelled, cleaning up resources...")
    finally:
        # stop the global GC manager
        await gc_manager.stop()

        # cancel all tasks (key fix)
        stdin_task.cancel()
        ws_task.cancel()
        if ota_task:
            ota_task.cancel()

        # wait for the tasks to finish (a timeout is required)
        await asyncio.wait(
            [stdin_task, ws_task, ota_task] if ota_task else [stdin_task, ws_task],
            timeout=3.0,
            return_when=asyncio.ALL_COMPLETED,
        )
        print("Server closed, program exiting.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted manually, program terminating.")
