"""Local gbrain service and scheduled dreams, supervised by launchd/systemd."""

import asyncio
import importlib
import json
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from ravn.adapters.triggers.cron import CronJob, CronTrigger


async def start_database(config: dict, directory: Path):
    kwargs = dict(config)
    module, cls = kwargs.pop("adapter").rsplit(".", 1)
    database = getattr(importlib.import_module(module), cls)(**kwargs)
    info = await database.start(str(directory / "postgres"))
    return (
        database,
        (
            f"postgresql://{quote(info.user)}:{quote(info.password, safe='')}@"
            f"{info.host}:{info.port}/{quote(info.dbname)}"
        ),
    )


async def run(config_path: Path) -> None:
    config = json.loads(config_path.read_text())
    env = {**os.environ, **config["environment"]}
    database = None
    if config.get("database"):
        database, env["GBRAIN_DATABASE_URL"] = await start_database(
            config["database"], config_path.parent
        )
    command = config["command"]
    server = await asyncio.create_subprocess_exec(
        *command, "serve", "--http", "--bind", "127.0.0.1", "--port", str(config["port"]), env=env
    )
    children = {server}
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)

    async def dream(_task):
        with (config_path.parent / "dream.log").open("a") as log:
            log.write(json.dumps({"started_at": datetime.now(UTC).isoformat()}) + "\n")
            log.flush()
            args = [part for phase in config["dream"]["phases"] for part in ("--phase", phase)]
            child = await asyncio.create_subprocess_exec(
                *command, "dream", "--json", *args, env=env, stdout=log, stderr=log
            )
            children.add(child)
            code = await child.wait()
            children.discard(child)
            log.write(
                json.dumps({"finished_at": datetime.now(UTC).isoformat(), "exit_code": code}) + "\n"
            )
        return True

    tasks = [asyncio.create_task(server.wait()), asyncio.create_task(stopped.wait())]
    if config["dream"]["enabled"]:
        scheduler = CronTrigger(
            [CronJob(name="dream", schedule=config["dream"]["schedule"], context="")],
            state_path=config_path.parent / "dream-state.json",
            lock_path=config_path.parent / "dream.lock",
        )
        tasks.append(asyncio.create_task(scheduler.run(dream)))
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for child in children:
        if child.returncode is None:
            child.terminate()
    await asyncio.gather(*(child.wait() for child in children))
    for task in tasks:
        if task not in done:
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if database is not None:
        await database.stop()
    for task in done:
        task.result()
    if not stopped.is_set():
        raise RuntimeError(f"gbrain service exited ({server.returncode})")


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1])))
