"""Standalone, process-isolated simulation attribution (stdlib runner).

Example (paths must contain src/simcord):
  python benchmarks/simulation_attribution.py --original /evidence/reference \
      --before /evidence/bd21e55 --output /evidence/new-run --smoke
Remove --smoke for nine randomized forward/reverse source batches. Add
--instrument profile or --instrument allocation for separate instrumented runs;
never compare those numbers with uninstrumented timing. Output directories must
not exist; raw JSON and profiles are exclusive-created and made read-only.

correctness-only is a *controlled candidate-source overlay*: current ownership
repairs remain, while original0083422 exact-search, wake recognition and local
index maintenance are restored, and emission always makes separate envelope and
history copies, as bd21e55 did. Stack-safe detach is shared with the candidate
because recursive deepcopy cannot support the required ancestry. Its traversal
efficiency is inseparable from this correctness baseline; this is not a pure
measurement of every ownership cost. No Git operations occur.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import cProfile
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
import traceback
import tracemalloc
from pathlib import Path

CONSTRUCTION = {
    "original0083422": "Unmodified supplied original source; ownership defects intentionally retained.",
    "bd21e55": "Unmodified supplied pre-copy-repair source, not the original baseline.",
    "candidate": "Current repository src, imported directly in a fresh subprocess.",
    "correctness-only": (
        "Copy candidate src; AST-select original _message_navigation, "
        "_wakes_recognized_wait, BackendBase._index_message/_drop_channel_messages; "
        "restore unconditional edit remove/reindex; remove obsolete index helpers; "
        "restore unconditional separate envelope/history copies (bd21e55 strategy). "
        "Keep stack-safe detach at original full-payload copying boundaries. "
        "This isolates old search/wake/index gains and new unused-envelope savings, "
        "but stack-safe traversal efficiency is inseparable from correctness."
    ),
}


def source_root(path):
    path = path.resolve()
    root = path / "src" if (path / "src" / "simcord").is_dir() else path
    if not (root / "simcord" / "__init__.py").is_file():
        raise ValueError(f"Not a simcord source directory: {path}")
    return root


def fingerprint(root):
    digest = hashlib.sha256()
    for path in sorted((root / "simcord").rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def save_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    path.chmod(0o444)


def definition(tree, name, owner=None):
    scope = (
        tree.body
        if owner is None
        else next(item.body for item in tree.body if isinstance(item, ast.ClassDef) and item.name == owner)
    )
    return next(
        item
        for item in scope
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )


def correctness_overlay(original, candidate, destination):
    shutil.copytree(candidate, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    replacements = {
        "preview/_queries.py": [(None, "_message_navigation")],
        "_settlement.py": [(None, "_wakes_recognized_wait")],
        "backend/ops/base.py": [("BackendBase", "_index_message"), ("BackendBase", "_drop_channel_messages")],
    }
    for relative, names in replacements.items():
        target = destination / "simcord" / relative
        lines = target.read_text().splitlines(keepends=True)
        old_text = (original / "simcord" / relative).read_text()
        old_tree, tree = ast.parse(old_text), ast.parse("".join(lines))
        patches = []
        for owner, name in names:
            node, old_node = definition(tree, name, owner), definition(old_tree, name, owner)
            patches.append(
                (
                    node.lineno - 1,
                    node.end_lineno,
                    old_text.splitlines(keepends=True)[old_node.lineno - 1 : old_node.end_lineno],
                )
            )
        if relative == "backend/ops/base.py":
            for name in ("_message_index_keys", "_remove_message_index"):
                node = definition(tree, name, "BackendBase")
                patches.append((node.lineno - 1, node.end_lineno, []))
            node = definition(tree, "emit", "BackendBase")
            patches.append(
                (
                    node.lineno - 1,
                    node.end_lineno,
                    [
                        "    def emit(self, event: str, payload: Mapping[str, Any]) -> None:\n",
                        "        envelope = detach(payload)\n",
                        "        self.transcript.append(('GATEWAY', event, detach(envelope)))\n",
                        "        for listener in self.subscribers:\n",
                        "            listener(event, envelope)\n",
                    ],
                )
            )
        for start, end, replacement in sorted(patches, reverse=True):
            lines[start:end] = replacement
        target.write_text("".join(lines))
    target = destination / "simcord/backend/ops/messages.py"
    lines = target.read_text().splitlines(keepends=True)
    method = definition(ast.parse("".join(lines)), "edit_message", "MessageMixin")
    old_id = next(
        node
        for node in method.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "old_id" for t in node.targets)
    )
    old_keys = next(
        node
        for node in method.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "old_keys" for t in node.targets)
    )
    reindex = next(
        node
        for node in method.body
        if isinstance(node, ast.If)
        and any(isinstance(n, ast.Name) and n.id == "old_keys" for n in ast.walk(node.test))
    )
    # Refuse a changed layout rather than silently construct a different variant.
    assert old_keys.lineno == old_id.end_lineno + 1
    lines[reindex.lineno - 1 : reindex.end_lineno] = ["        self._index_message(message)\n"]
    lines[old_id.lineno - 1 : old_keys.end_lineno] = ["        self._index_message(message, remove=True)\n"]
    target.write_text("".join(lines))
    return destination


def clock():
    return time.perf_counter(), time.process_time()


def elapsed(start):
    return {"wall_s": time.perf_counter() - start[0], "cpu_s": time.process_time() - start[1]}


async def operations(count, rich, discord, simcord):
    seen = []

    class Bot(discord.Client):
        async def on_message(self, message):
            if message.author.bot:
                return
            index = int(message.content.split()[1])
            asyncio.get_running_loop().call_soon(seen.append, index)
            await asyncio.sleep(0)
            kwargs = {}
            if rich:
                embed = discord.Embed(title=f"card {index}", description="rich payload " * 12)
                embed.add_field(name="field", value="nested value")
                view = discord.ui.View(timeout=None)
                view.add_item(discord.ui.Button(label="Button", custom_id=f"button:{index}"))
                kwargs = {"embed": embed, "view": view}
            await message.reply(f"reply {index}", **kwargs)

    setup = clock()
    async with simcord.run(Bot(intents=discord.Intents.all())) as env:
        guild = env.create_guild("isolated")
        channel = guild.create_text_channel("general")
        actor = guild.add_member(env.create_user("alice"))
        setup_time = elapsed(setup)
        start = clock()
        for index in range(count):
            await actor.send(channel, f"ping {index}")
            assert seen == list(range(index + 1))
            last = channel.last_message
            assert last is not None and last.content == f"reply {index}"
            assert last.reference is not None
            if rich:
                assert last.embeds[0].title == f"card {index}"
                assert last.components[0].children[0].custom_id == f"button:{index}"
            await last.edit(content=f"edited {index}")
            assert channel.last_message.content == f"edited {index}"
        history = channel.history()
        assert [m.content for m in history] == [s for i in range(count) for s in (f"ping {i}", f"edited {i}")]
        assert not env.errors
        work_time = elapsed(start)
        result = {
            "messages": len(history),
            "callbacks": len(seen),
            "edits": count,
            "virtual_s": env.backend.clock.elapsed,
            "setup": setup_time,
            "operations": work_time,
        }
        if tracemalloc.is_tracing():
            current, peak = tracemalloc.get_traced_memory()
            result["live_world_allocation"] = {"retained_bytes": current, "peak_bytes": peak}
        if rich:
            history[-1].embeds[0].to_dict()["fields"][0]["value"] = "SDK mutation"
            result["sdk_isolated"] = (
                channel.last_message.embeds[0].fields[0].value == "nested value"
                and env.backend.get_message(channel.id, last.id).embeds[0]["fields"][0]["value"]
                == "nested value"
            )
    return result


async def search(count, discord, simcord):
    from simcord.preview._queries import _message_navigation

    setup = clock()
    async with simcord.run(discord.Client(intents=discord.Intents.all())) as env:
        guild = env.create_guild("search")
        channel = guild.create_text_channel("general")
        actor = guild.add_member(env.create_user("alice"))
        native = env.bot.get_channel(channel.id)
        messages = [await native.send(f"needle {index:04} Café ||hiddenmarker||") for index in range(count)]
        async with env.preview(channel, viewers=[actor]) as preview:
            page = preview._open_page(actor.id)
            setup_time = elapsed(setup)
            start = clock()
            results = {}
            for query in ("needle", "absentmarker", "café", "hiddenmarker"):
                page.navigation_query = query
                page.navigation_cursor = None
                ids, pages = [], 0
                query_start = clock()
                first_page = None
                while True:
                    rows, nav = _message_navigation(preview, page)
                    if first_page is None:
                        first_page = elapsed(query_start)
                    ids.extend(int(row["id"]) for row in rows)
                    pages += 1
                    if not nav["hasNext"]:
                        assert nav["nextCursor"] is None
                        break
                    assert nav["nextCursor"]
                    page.navigation_cursor = nav["nextCursor"]
                expected = [m.id for m in messages] if query in ("needle", "café") else []
                assert ids == expected
                results[query] = {
                    "matches": len(ids),
                    "pages": pages,
                    "first_page": first_page,
                    "all_pages": elapsed(query_start),
                }
            assert not env.errors
            return {"queries": results, "setup": setup_time, "operations": elapsed(start)}


async def settlement(count, discord, simcord):
    seen, roots = [], []

    class Bot(discord.Client):
        async def on_message(self, message):
            if message.content == "park":
                roots.extend(asyncio.create_task(self.wait_for("release")) for _ in range(count))
            elif message.content == "go":
                loop = asyncio.get_running_loop()
                for index in range(128):
                    loop.call_later(0.02, seen.append, index)

    setup = clock()
    async with simcord.run(Bot(intents=discord.Intents.all())) as env:
        guild = env.create_guild("settlement")
        channel = guild.create_text_channel("timers")
        actor = guild.add_member(env.create_user("alice"))
        await actor.send(channel, "park")
        setup_time = elapsed(setup)
        start = clock()
        await actor.send(channel, "go")
        operation_time = elapsed(start)
        assert seen == list(range(128))
        assert all(not root.done() for root in roots)
        env.bot.dispatch("release", "ok")
        await env.settle()
        assert all(root.result() == "ok" for root in roots)
        assert not env.errors
    return {"callbacks": len(seen), "released": len(roots), "setup": setup_time, "operations": operation_time}


def ancestry(depth):
    from simcord.backend import Backend

    setup = clock()
    backend = Backend()
    guild = backend.create_guild("depth")
    channel = backend.create_channel(guild.id, "depth")
    previous = None
    for index in range(depth):
        previous = backend.create_message(
            channel.id,
            backend.bot_user.id,
            str(index),
            broadcast=False,
            reference={"message_id": str(previous.id)} if previous is not None else None,
        )
    setup_time = elapsed(setup)
    assert not backend.subscribers
    start = clock()
    backend.announce_message_create(previous)
    payload = backend.transcript[-1][2]
    for index in reversed(range(depth)):
        assert payload["content"] == str(index)
        payload = payload.get("referenced_message")
    assert payload is None
    return {
        "depth": depth,
        "retained_nodes": depth,
        "subscribers": 0,
        "setup": setup_time,
        "operations": elapsed(start),
    }


def ownership():
    from simcord.backend import Backend

    backend = Backend()
    guild = backend.create_guild("ownership")
    channel = backend.create_channel(guild.id, "ownership")
    embeds = [{"title": "original", "fields": [{"name": "n", "value": "v"}]}]
    message = backend.create_message(channel.id, backend.bot_user.id, "x", embeds=embeds)
    embeds[0]["fields"][0]["value"] = "input mutation"
    input_isolated = message.embeds[0]["fields"][0]["value"] == "v"
    live = []
    backend.subscribers.append(lambda event, payload: live.append(payload))
    payload = {"nested": [{"value": "before"}]}
    backend.emit("OWNERSHIP_PROBE", payload)
    retained = backend.transcript[-1][2]
    live[0]["nested"][0]["value"] = "subscriber mutation"
    return {
        "input_isolated": input_isolated,
        "canonical_isolated": payload["nested"][0]["value"] == "before",
        "history_isolated": retained["nested"][0]["value"] == "before",
    }


def worker(args):
    root = source_root(args.source)
    sys.path.insert(0, str(root))
    start = clock()
    import discord

    import simcord

    import_time = elapsed(start)
    assert Path(simcord.__file__).resolve().is_relative_to(root)
    result = {
        "source": str(root),
        "imported_source": simcord.__file__,
        "source_sha256": fingerprint(root),
        "variant": args.variant,
        "construction": CONSTRUCTION[args.variant],
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "dependencies": {
            p: importlib.metadata.version(p) for p in ("discord.py", "sortedcontainers", "aiohttp", "attrs")
        },
        "import": import_time,
        "recursion_limit": sys.getrecursionlimit(),
        "case": args.case,
        "instrument": args.instrument,
    }
    try:
        result["ownership"] = ownership()
        if args.variant in ("candidate", "correctness-only"):
            assert all(result["ownership"].values()), result["ownership"]
        gc.collect()
        profiler = cProfile.Profile() if args.instrument == "profile" else None
        if args.instrument == "allocation":
            tracemalloc.start()
        if profiler:
            profiler.enable()
        start = clock()
        if args.case == "ancestry":
            completed = ancestry(20 if args.smoke else 750)
        elif args.case == "search":
            completed = asyncio.run(search(101 if args.smoke else 401, discord, simcord))
        elif args.case == "settlement":
            completed = asyncio.run(settlement(3 if args.smoke else 400, discord, simcord))
        else:
            completed = asyncio.run(
                operations(3 if args.smoke else 100, args.case == "rich", discord, simcord)
            )
        if args.case == "rich" and args.variant in ("candidate", "correctness-only"):
            assert completed["sdk_isolated"], completed
        result.update({"total": elapsed(start), "completed": completed, "status": "ok"})
    except Exception:
        result.update({"status": "error", "error": traceback.format_exc()})
    finally:
        if args.instrument == "allocation" and tracemalloc.is_tracing():
            current, peak = tracemalloc.get_traced_memory()
            result["allocation"] = {"retained_bytes_before_gc": current, "peak_bytes": peak}
            gc.collect()
            result["allocation"]["retained_bytes_after_gc"] = tracemalloc.get_traced_memory()[0]
            tracemalloc.stop()
        if args.instrument == "profile" and "profiler" in locals() and profiler:
            profiler.disable()
            profile_path = args.result.with_suffix(".prof")
            with profile_path.open("xb") as stream:
                import marshal

                profiler.create_stats()
                marshal.dump(profiler.stats, stream)
            profile_path.chmod(0o444)
            result["profile"] = str(profile_path)
    save_json(args.result, result)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--original", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--candidate", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--instrument", choices=("timing", "allocation", "profile"), default="timing")
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=("plain", "rich", "ancestry", "search", "settlement"),
        default=["plain", "rich", "ancestry", "search", "settlement"],
    )
    parser.add_argument("--variant", choices=tuple(CONSTRUCTION), help=argparse.SUPPRESS)
    parser.add_argument("--source", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--case", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.source:
        worker(args)
        return
    if args.original is None or args.output is None or args.rounds < 1:
        parser.error("--original and --output required; --rounds must be positive")
    original, candidate = source_root(args.original), source_root(args.candidate)
    args.output.mkdir(parents=True, exist_ok=False)
    variants = {
        "original0083422": original,
        "correctness-only": correctness_overlay(original, candidate, args.output / "correctness-only-src"),
        "candidate": candidate,
    }
    if args.before:
        variants["bd21e55"] = source_root(args.before)
    hashes = {name: fingerprint(root) for name, root in variants.items()}
    manifest = {
        "sources": {
            name: {"path": str(root), "sha256": hashes[name], "construction": CONSTRUCTION[name]}
            for name, root in variants.items()
        },
        "seed": args.seed,
        "rounds": 1 if args.smoke else args.rounds,
        "instrument": args.instrument,
        "smoke": args.smoke,
        "scope": "Fresh interpreter per sample. Initial discord/simcord imports timed separately. "
        "Total includes lazy imports, asyncio.run, Env setup, operations, validation and teardown; "
        "setup and operation spans also reported. "
        "Parent process launch time excluded. Ownership probe and pre-sample gc excluded. "
        "Allocation/profile are separate modes, never combined with clean timing. "
        "Allocation covers complete workloads, including setup; worker retained bytes are "
        "post-teardown; plain/rich also record live-world retained and peak before SDK mutation. "
        "Search calls actual signed-cursor navigation without HTTP.",
        "order": [],
        "samples": [],
    }
    save_json(args.output / "construction.json", manifest)
    rng = random.Random(args.seed)
    for round_number in range(manifest["rounds"]):
        order = list(variants)
        rng.shuffle(order)
        # Balanced randomized ABBA-style batches: ABCD, DCBA, reshuffled next round.
        for name in order + order[::-1]:
            cases = list(args.cases)
            rng.shuffle(cases)
            for case in cases:
                sample_id = f"{len(manifest['samples']):04}-{name}-{case}"
                result_path = args.output / f"{sample_id}.json"
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--source",
                    str(variants[name]),
                    "--variant",
                    name,
                    "--case",
                    case,
                    "--result",
                    str(result_path),
                    "--instrument",
                    args.instrument,
                ]
                if args.smoke:
                    command.append("--smoke")
                process = subprocess.run(
                    command, capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": "0"}
                )
                save_json(
                    args.output / f"{sample_id}.process.json",
                    {
                        "command": command,
                        "returncode": process.returncode,
                        "stdout": process.stdout,
                        "stderr": process.stderr,
                    },
                )
                if not result_path.exists():
                    raise RuntimeError(f"Worker did not produce {result_path}; inspect process JSON")
                row = json.loads(result_path.read_text())
                if row["source_sha256"] != hashes[name]:
                    raise RuntimeError(f"Source changed during measurement: {name}")
                manifest["order"].append({"round": round_number, "variant": name, "case": case})
                manifest["samples"].append(
                    {
                        "file": result_path.name,
                        "variant": name,
                        "case": case,
                        "status": row["status"],
                        "total": row.get("total"),
                    }
                )
    manifest["all_completed"] = all(row["status"] == "ok" for row in manifest["samples"])
    save_json(args.output / "report.json", manifest)
    print(
        json.dumps(
            {
                "report": str(args.output / "report.json"),
                "all_completed": manifest["all_completed"],
                "samples": len(manifest["samples"]),
            }
        )
    )
    if not manifest["all_completed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
