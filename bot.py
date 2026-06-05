import asyncio
import os
import platform
import subprocess
import time
from pathlib import Path

import discord
import psutil
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
STARTUP_CHANNEL_ID = os.getenv("STARTUP_CHANNEL_ID")

PROJECTS_DIR = Path(
    os.getenv("PROJECTS_DIR", "/home/tengis/Documents/Tengis")
).expanduser().resolve()

DEFAULT_COMMIT_MESSAGE = "auto: update from local discord bot"
MAX_DISCORD_MESSAGE = 1800


class LocalBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.startup_message_sent = False

    async def setup_hook(self):
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Synced {len(synced)} commands to guild {DISCORD_GUILD_ID}")
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global commands")


client = LocalBot()


def make_git_env() -> dict[str, str]:
    env = os.environ.copy()

    # Prevent git from asking Username/Password and freezing the bot.
    env["GIT_TERMINAL_PROMPT"] = "0"

    # Force SSH to fail fast instead of asking interactively.
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"

    return env


def run_command_blocking(
    command: list[str],
    cwd: Path,
    timeout: int = 180,
) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=make_git_env(),
            stdin=subprocess.DEVNULL,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 124, "Command timed out."


async def run_command(
    command: list[str],
    cwd: Path,
    timeout: int = 180,
) -> tuple[int, str]:
    return await asyncio.to_thread(run_command_blocking, command, cwd, timeout)


def truncate_message(text: str) -> str:
    if len(text) <= MAX_DISCORD_MESSAGE:
        return text

    return text[:MAX_DISCORD_MESSAGE] + "\n... output truncated"


def is_git_repo(path: Path) -> bool:
    return path.is_dir() and (path / ".git").exists()


def list_git_repos() -> list[Path]:
    if not PROJECTS_DIR.exists():
        return []

    repos = []

    for item in PROJECTS_DIR.iterdir():
        if is_git_repo(item):
            repos.append(item)

    return sorted(repos, key=lambda p: p.name.lower())


def safe_repo_path(repo_name: str) -> Path | None:
    repo_path = (PROJECTS_DIR / repo_name).resolve()

    try:
        repo_path.relative_to(PROJECTS_DIR)
    except ValueError:
        return None

    if not is_git_repo(repo_path):
        return None

    return repo_path


def protect_local_secrets(repo_path: Path) -> None:
    info_dir = repo_path / ".git" / "info"
    exclude_file = info_dir / "exclude"

    if not info_dir.exists():
        return

    existing = exclude_file.read_text(errors="ignore") if exclude_file.exists() else ""

    rules = [
        ".env",
        ".venv/",
        "__pycache__/",
        "*.pyc",
        ".DS_Store",
    ]

    with exclude_file.open("a", encoding="utf-8") as file:
        for rule in rules:
            if rule not in existing:
                file.write(f"\n{rule}")


async def get_current_branch(repo_path: Path) -> str | None:
    code, output = await run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        timeout=30,
    )

    if code != 0:
        return None

    branch = output.strip()

    if branch == "HEAD":
        return None

    return branch


async def has_origin_remote(repo_path: Path) -> bool:
    code, _ = await run_command(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_path,
        timeout=30,
    )
    return code == 0


async def has_upstream(repo_path: Path) -> bool:
    code, _ = await run_command(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=repo_path,
        timeout=30,
    )
    return code == 0


async def repo_status_text(repo_path: Path) -> str:
    code, output = await run_command(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        timeout=30,
    )

    if code != 0:
        return f"{repo_path.name}: status failed"

    if not output:
        return f"{repo_path.name}: clean"

    changed_lines = output.splitlines()
    return f"{repo_path.name}: {len(changed_lines)} changed file(s)"


async def remote_info_text(repo_path: Path) -> str:
    code, output = await run_command(
        ["git", "remote", "-v"],
        cwd=repo_path,
        timeout=30,
    )

    if code != 0:
        return f"{repo_path.name}: no remote"

    if "https://github.com/" in output:
        return f"{repo_path.name}: HTTPS remote detected, change to SSH"

    if "git@github.com:" in output:
        return f"{repo_path.name}: SSH remote OK"

    return f"{repo_path.name}: remote exists"


async def update_one_repo(repo_path: Path, commit_message: str) -> str:
    protect_local_secrets(repo_path)

    code, status = await run_command(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        timeout=30,
    )

    if code != 0:
        return f"{repo_path.name}: status failed"

    if not status:
        return f"{repo_path.name}: clean, skipped"

    code, add_output = await run_command(
        ["git", "add", "-A"],
        cwd=repo_path,
        timeout=60,
    )

    if code != 0:
        return f"{repo_path.name}: add failed: {add_output[:160]}"

    code, commit_output = await run_command(
        ["git", "commit", "-m", commit_message],
        cwd=repo_path,
        timeout=120,
    )

    if code != 0:
        return f"{repo_path.name}: commit failed: {commit_output[:160]}"

    branch = await get_current_branch(repo_path)

    if branch is None:
        return f"{repo_path.name}: committed, but branch is detached/unknown. Push manually."

    if await has_upstream(repo_path):
        code, push_output = await run_command(
            ["git", "push"],
            cwd=repo_path,
            timeout=180,
        )
    else:
        if not await has_origin_remote(repo_path):
            return f"{repo_path.name}: committed, but no origin remote."

        code, push_output = await run_command(
            ["git", "push", "-u", "origin", branch],
            cwd=repo_path,
            timeout=180,
        )

    if code != 0:
        return f"{repo_path.name}: push failed: {push_output[:220]}"

    hash_code, short_hash = await run_command(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo_path,
        timeout=30,
    )

    if hash_code != 0:
        short_hash = "unknown"

    return f"{repo_path.name}: committed and pushed {short_hash}"


async def send_startup_message() -> None:
    if not STARTUP_CHANNEL_ID:
        print("STARTUP_CHANNEL_ID is missing. Startup message skipped.")
        return

    try:
        channel_id = int(STARTUP_CHANNEL_ID)

        channel = client.get_channel(channel_id)

        if channel is None:
            channel = await client.fetch_channel(channel_id)

        startup_text = (
            "🟢 **Local Discord Bot аслаа**\n"
            f"Host: `{platform.node()}`\n"
            f"Projects: `{PROJECTS_DIR}`\n"
            f"Time: `{time.strftime('%Y-%m-%d %H:%M:%S')}`"
        )

        await channel.send(startup_text)
        print(f"Startup message sent to channel {channel_id}")

    except Exception as error:
        print(f"Failed to send startup message: {error}")


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    print(f"PROJECTS_DIR = {PROJECTS_DIR}")

    if not client.startup_message_sent:
        client.startup_message_sent = True
        await send_startup_message()


@client.tree.command(name="ping", description="Check whether the bot is online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong")


@client.tree.command(name="pc", description="Show laptop status")
async def pc(interaction: discord.Interaction):
    uptime_seconds = int(time.time() - psutil.boot_time())
    uptime_hours = uptime_seconds // 3600
    uptime_minutes = (uptime_seconds % 3600) // 60

    cpu = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    message = (
        "```text\n"
        f"Host: {platform.node()}\n"
        f"OS: {platform.system()} {platform.release()}\n"
        f"Uptime: {uptime_hours}h {uptime_minutes}m\n"
        f"CPU: {cpu}%\n"
        f"RAM: {memory.percent}% used\n"
        f"Disk: {disk.percent}% used\n"
        f"Projects dir: {PROJECTS_DIR}\n"
        "```"
    )

    await interaction.response.send_message(message)


@client.tree.command(name="projects", description="List folders inside Tengis directory")
async def projects(interaction: discord.Interaction):
    if not PROJECTS_DIR.exists():
        await interaction.response.send_message(f"`{PROJECTS_DIR}` folder not found.")
        return

    folders = sorted([p.name for p in PROJECTS_DIR.iterdir() if p.is_dir()])[:50]

    if not folders:
        await interaction.response.send_message("No folders found.")
        return

    await interaction.response.send_message(
        "```text\n" + "\n".join(folders) + "\n```"
    )


@client.tree.command(name="repos", description="List git repositories inside Tengis directory")
async def repos(interaction: discord.Interaction):
    git_repos = list_git_repos()

    if not git_repos:
        await interaction.response.send_message("No git repositories found.")
        return

    names = [repo.name for repo in git_repos]

    await interaction.response.send_message(
        "```text\n" + "\n".join(names) + "\n```"
    )


@client.tree.command(name="repo_status", description="Show status for one repo or all repos")
@app_commands.describe(repo_name="Optional: repo folder name. Leave empty to check all repos.")
async def repo_status(interaction: discord.Interaction, repo_name: str = ""):
    await interaction.response.defer(thinking=True)

    if repo_name.strip():
        repo_path = safe_repo_path(repo_name.strip())

        if repo_path is None:
            await interaction.followup.send(
                f"`{repo_name}` is not a git repository inside `{PROJECTS_DIR}`."
            )
            return

        result = await repo_status_text(repo_path)
        await interaction.followup.send("```text\n" + result + "\n```")
        return

    git_repos = list_git_repos()

    if not git_repos:
        await interaction.followup.send("No git repositories found.")
        return

    results = []

    for repo in git_repos:
        results.append(await repo_status_text(repo))

    final_message = "\n".join(results)

    await interaction.followup.send(
        "```text\n" + truncate_message(final_message) + "\n```"
    )


@client.tree.command(name="repo_remote", description="Check whether repos use SSH or HTTPS remote")
@app_commands.describe(repo_name="Optional: repo folder name. Leave empty to check all repos.")
async def repo_remote(interaction: discord.Interaction, repo_name: str = ""):
    await interaction.response.defer(thinking=True)

    if repo_name.strip():
        repo_path = safe_repo_path(repo_name.strip())

        if repo_path is None:
            await interaction.followup.send(
                f"`{repo_name}` is not a git repository inside `{PROJECTS_DIR}`."
            )
            return

        result = await remote_info_text(repo_path)
        await interaction.followup.send("```text\n" + result + "\n```")
        return

    git_repos = list_git_repos()

    if not git_repos:
        await interaction.followup.send("No git repositories found.")
        return

    results = []

    for repo in git_repos:
        results.append(await remote_info_text(repo))

    final_message = "\n".join(results)

    await interaction.followup.send(
        "```text\n" + truncate_message(final_message) + "\n```"
    )


@client.tree.command(name="update_repo", description="Commit and push one repo or all repos")
@app_commands.describe(
    repo_name="Optional: repo folder name. Leave empty to update all repos.",
    message="Optional commit message.",
)
async def update_repo(
    interaction: discord.Interaction,
    repo_name: str = "",
    message: str = DEFAULT_COMMIT_MESSAGE,
):
    await interaction.response.defer(thinking=True)

    commit_message = message.strip() or DEFAULT_COMMIT_MESSAGE

    if repo_name.strip():
        repo_path = safe_repo_path(repo_name.strip())

        if repo_path is None:
            await interaction.followup.send(
                f"`{repo_name}` is not a git repository inside `{PROJECTS_DIR}`."
            )
            return

        result = await update_one_repo(repo_path, commit_message)

        await interaction.followup.send(
            "```text\n" + truncate_message(result) + "\n```"
        )
        return

    git_repos = list_git_repos()

    if not git_repos:
        await interaction.followup.send("No git repositories found.")
        return

    results = []

    for repo_path in git_repos:
        results.append(await update_one_repo(repo_path, commit_message))

    final_message = "\n".join(results)

    await interaction.followup.send(
        "```text\n" + truncate_message(final_message) + "\n```"
    )


@client.tree.command(name="startup_test", description="Send startup message manually for testing")
async def startup_test(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await send_startup_message()
    await interaction.followup.send("Startup message test done.")


if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add DISCORD_TOKEN to .env")

client.run(DISCORD_TOKEN)
