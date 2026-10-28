import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
from pathlib import Path

from pydantic import ValidationError

from otgw.app import App
from otgw.config import Config, load_config, load_env_file


async def _run(app: App) -> None:
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    with contextlib.suppress(asyncio.CancelledError):
        await app.run()


async def _monitor(app: App, seconds: float) -> None:
    """Read the gateway without MQTT and print every value that is published."""

    async def show() -> None:
        while True:
            topic, payload, _ = await app.queue.get()
            if topic.startswith(app.prefix + "/"):  # not the discovery
                print(f"{topic:45} {payload}", flush=True)

    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(seconds), asyncio.TaskGroup() as group:
            group.create_task(app.gateway())
            group.create_task(show())


def main() -> None:
    parser = argparse.ArgumentParser(prog="otgw", description="OpenTherm Gateway to MQTT and Home Assistant")
    parser.add_argument("-c", "--config", default="otgw.yaml", help="configuration file")
    parser.add_argument(
        "--env-file",
        help="file with OTGW_MQTT_* settings (default: .env in the current directory, if present)",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    )
    parser.add_argument("--check-config", action="store_true", help="validate the configuration and exit")
    parser.add_argument(
        "--dump-discovery", action="store_true", help="print Home Assistant discovery payloads and exit"
    )
    parser.add_argument(
        "--monitor",
        nargs="?",
        type=float,
        const=60,
        metavar="SECONDS",
        help="print the values read from the gateway (default 60 seconds) without MQTT, then exit",
    )
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    env_file = args.env_file or (".env" if Path(".env").exists() else None)
    try:
        if env_file:
            load_env_file(env_file)
    except (OSError, ValueError) as error:
        print(f"env file: {error}", file=sys.stderr)
        sys.exit(1)

    if args.monitor is not None and not Path(args.config).exists():
        config = Config()  # monitoring works without a configuration (default serial device)
    else:
        try:
            config = load_config(args.config)
        except (OSError, ValueError, ValidationError) as error:
            print(f"{args.config}: {error}", file=sys.stderr)
            sys.exit(1)

    if args.check_config:
        print(f"{args.config}: OK")
        return

    if args.dump_discovery:
        print(json.dumps(App(config).discovery(), indent=2, ensure_ascii=False))
        return

    if args.monitor is not None:
        print(f"monitoring the gateway for {args.monitor:.0f}s...", file=sys.stderr)
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_monitor(App(config), args.monitor))
    else:
        asyncio.run(_run(App(config)))


if __name__ == "__main__":
    main()
