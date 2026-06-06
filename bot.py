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
from google import genai
from google.genai import types


# .env file-iig achaalj baina.
# Ene dotroos bot token, guild id, startup channel id, owner id geh met nuuts utguudiig avna.
load_dotenv()


# Discord bot ajillah token.
# Ene token baihgui bol bot asahgui.
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")


# Guild id bol chinii Discord serveriin id.
# Ene baiwal slash command-uud ter server deer hurdan sync hiigddeg.
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")


# Startup message yvuulah channel id.
# Bot asah ued ene channel ruu aslaa gedeg message yvuulna.
STARTUP_CHANNEL_ID = os.getenv("STARTUP_CHANNEL_ID")


# Gemini API key.
# Ene key-r bot Gemini model ruu prompt yvuulna.
# Baihgui bol /ask command ajillahgui, busad command hevendee ajillana.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


# Ashiglah Gemini model.
# Default n gemini-3.5-flash.
# Daraa n .env dotor soliod bot restart hiih bolomjtoi.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


# Owner id bol zovhon chamd command ashigluulah hamgaalalt.
# Ene id .env file dotor DISCORD_OWNER_ID gej baih yostoi.
DISCORD_OWNER_ID_RAW = os.getenv("DISCORD_OWNER_ID")


# Git project-uud baigaa gol folder.
# .env dotor PROJECTS_DIR baihgui bol default-aar ene zamaar ajillana.
PROJECTS_DIR = Path(
    os.getenv("PROJECTS_DIR", "/home/tengis/Documents/Tengis")
).expanduser().resolve()


# Auto commit hiih ued ashiglah default commit message.
DEFAULT_COMMIT_MESSAGE = "auto: update from local discord bot"


# Discord deer neg message heterhii urt bol aldaa gardag.
# Tiimees output-iig 1800 temdegt deer tasalna.
MAX_DISCORD_MESSAGE = 1800


# AI chat history-d heden turn hadgalahig zaana.
# 1 turn gedeg n user-iin 1 asuult + assistant-iin 1 hariult gesen ug.
# 8 bol suuliin 8 udaa /ask hiisen asuult hariultiig hadgalna.
AI_MAX_HISTORY_TURNS = 8


# AI chat memory.
# Bot restart hiivel ene memory alga bolno.
# Key n Discord user id, value n chat history baina.
ai_chat_memory: dict[int, list[tuple[str, str]]] = {}


# Shutdown hiih sudo command.
# Ene command /usr/local/sbin/discord-poweroff script-iig sudo-r ajilluulna.
POWER_OFF_COMMAND = [
    "/usr/bin/sudo",
    "-n",
    "/usr/local/sbin/discord-poweroff",
]


# Shutdown-g heden minut hurtel hoishluulj bolohiig hyazgaarlaj baina.
# Ene ni sanamsargui mash ih hugatsaagaar schedule hiih erdeliig bagasgana.
MAX_SHUTDOWN_MINUTES = 120


# Python task dotooddoo shutdown huleej baigaa esehiig hadgalna.
# Cancel command ashiglahad ene task-iig zogsoono.
pending_shutdown_task: asyncio.Task | None = None


# Bot asahaas omno owner id zov esehiig shalgana.
# Owner id baihgui bol hamgaalaltgui bot asah ersdeltei tul shuud zogsoono.
if not DISCORD_OWNER_ID_RAW:
    raise RuntimeError("DISCORD_OWNER_ID is missing. Add DISCORD_OWNER_ID to .env")


try:
    DISCORD_OWNER_ID = int(DISCORD_OWNER_ID_RAW)
except ValueError:
    raise RuntimeError("DISCORD_OWNER_ID must be a number.")


class NotOwnerError(app_commands.CheckFailure):
    # Ene custom error n owner bish hun command ashiglah ued ashiglagdana.
    # Error handler deer ene aldaag barij avaad oilgomjtoi hariu yvuulna.
    pass


class LocalBot(discord.Client):
    # Ene class n discord.Client deer suurilsan local bot.
    # CommandTree ashiglaad slash command-uudaa burtgene.

    def __init__(self):
        # Default intents n ene bot-d hangalttai.
        # Message content unshih shaardlagagui uchraas iluu intent idevhjuulehgui.
        intents = discord.Intents.default()

        super().__init__(intents=intents)

        # Slash command-uud end hadgalagdana.
        self.tree = app_commands.CommandTree(self)

        # Startup message neg l udaa yvuulahad ashiglana.
        self.startup_message_sent = False

    async def setup_hook(self):
        # Bot login hiisnii daraa command-uud sync hiine.
        # Guild id baival command ter server deer hurdan shinechlegdene.
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Synced {len(synced)} commands to guild {DISCORD_GUILD_ID}")
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global commands")


client = LocalBot()


# Gemini client.
# Discord client-iin ner client gej baigaa tul Gemini-g gemini_ai gej nerlej baina.
# Ingevel ner davhtsahgui, code oilgomjtoi baina.
gemini_ai = None


if GEMINI_API_KEY:
    gemini_ai = genai.Client(api_key=GEMINI_API_KEY)


def owner_only():
    # Ene decorator n command buriin omno owner id shalgana.
    # Zovhon .env dotorh DISCORD_OWNER_ID-tai taarval command ajillana.

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id != DISCORD_OWNER_ID:
            raise NotOwnerError()

        return True

    return app_commands.check(predicate)


@client.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    # Ene handler n slash command deer garsan aldaag neg gazraas zohitsuulna.
    # Owner bish hun command ashiglavl endees hariu yvuulna.

    if isinstance(error, NotOwnerError):
        message = "Ene command zovhon owner-d zoriulagdsan."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

        return

    # Busad aldaag terminal deer hevlej, Discord deer tovch hariu yvuulna.
    print(f"Command error: {error}")

    message = f"Command ajilluulah ued aldaa garlaa: {error}"

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def make_git_env() -> dict[str, str]:
    # Git command ajillah orchnii huvisagchdiig beldene.
    # Git username/password asuugaad bot-iig gatsahaas hamgaalna.

    env = os.environ.copy()

    # Git terminal deer username/password asuuhgui.
    env["GIT_TERMINAL_PROMPT"] = "0"

    # SSH interactive prompt gargahgui.
    # Key buruu esvel permission dutuu bol hurdan fail hiine.
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"

    return env


def run_command_blocking(
    command: list[str],
    cwd: Path,
    timeout: int = 180,
) -> tuple[int, str]:
    # Ene function n terminal command-iig blocking helbereer ajilluulna.
    # Daraagaar ni asyncio.to_thread ashiglaad async orchind gatsahgui ajilluulna.

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

    except FileNotFoundError as error:
        return 127, f"Command not found: {error.filename}"


async def run_command(
    command: list[str],
    cwd: Path,
    timeout: int = 180,
) -> tuple[int, str]:
    # Ene async wrapper n blocking command-iig tusdaa thread deer ajilluulna.
    # Ingeseer bot busad command avah bolomjtoi hevendee baina.

    return await asyncio.to_thread(run_command_blocking, command, cwd, timeout)


def truncate_message(text: str) -> str:
    # Discord message heterhii urt bol aldaa gardag.
    # Tiimees output-iig togtooson hemjeend tasalna.

    if len(text) <= MAX_DISCORD_MESSAGE:
        return text

    return text[:MAX_DISCORD_MESSAGE] + "\n... output truncated"


def build_agent_system_prompt() -> str:
    # Ene system prompt n Gemini-g general AI assistant bolgono.
    # Gol durem n command execute hiihgui, busdaar bol engiin AI assistant shig tuslana.
    # Hariultaa dandaa Mongol kirilleer ogohig shaardaj baina.

    return (
        "Чи бол хэрэглэгчийн хувийн ерөнхий AI туслах. "
        "Чи зөвхөн coding, Linux, git, system architecture биш бүх төрлийн сэдэв дээр тусална. "
        "Жишээ нь өдөр тутмын асуулт, сурах зүйл, англи хэл, маркетинг, дизайн, бичвэр найруулах, санаа гаргах, төлөвлөгөө боловсруулах, тайлбарлах чиглэлээр тусална. "
        "Хэрэглэгч Latin Mongol эсвэл Монгол кириллээр бичсэн байж болно. "
        "Гэхдээ чи үргэлж Монгол кириллээр хариулна. "
        "Хариулт ойлгомжтой, шууд хэрэгжүүлэхэд амар, хэт нуршуу биш байна. "
        "Хэрэглэгч код асуувал кодыг code block дотор өгч болно. "
        "Чи энэ Discord bot-оор дамжуулж laptop дээр command шууд ажиллуулахгүй. "
        "Чи shutdown, update_repo, file delete, system өөрчлөх зэрэг бодит үйлдлийг өөрөө execute хийхгүй. "
        "Ийм үйлдэл хийх шаардлагатай бол хэрэглэгч тусдаа slash command ашиглаж баталгаатай ажиллуулна. "
        "Чи боломжтой үед bot-д байгаа slash command-уудыг зөвлөж болно: /pc, /projects, /repos, /repo_status, /repo_remote, /update_repo, /startup_test, /ask, /ai_model, /ai_reset, /shutdown, /cancel_shutdown. "
        "Гэхдээ эдгээрийг чи өөрөө ажиллуулсан мэт дүр эсгэж болохгүй. "
        "Мэдэхгүй зүйл байвал зохиож хэлэхгүй, мэдэхгүй гэдгээ шууд хэлнэ. "
        "Хэрэв асуулт шинэ мэдээ, үнэ, хууль, schedule, current event зэрэг байвал мэдээлэл шинэчлэгдсэн байж магадгүй гэдгийг анхааруулна."
    )


def build_ai_conversation_text(user_id: int, new_prompt: str) -> str:
    # Ene function n umnuh chat history-g shine prompt-toi ni neg text bolgoj Gemini ruu yvuulna.
    # Ingeseer /ask command n baga zereg memory-toi hariltsaa shig ajillana.

    history = ai_chat_memory.get(user_id, [])

    parts = []

    for role, text in history:
        if role == "user":
            parts.append(f"User: {text}")
        else:
            parts.append(f"Assistant: {text}")

    parts.append(f"User: {new_prompt}")

    return "\n\n".join(parts)


def remember_ai_chat(user_id: int, user_prompt: str, assistant_answer: str) -> None:
    # Ene function n AI chat history-g hadgalna.
    # 1 turn gedeg n user-iin 1 asuult + assistant-iin 1 hariult gesen ug.
    # AI_MAX_HISTORY_TURNS = 8 gevel suuliin 8 udaa /ask hiisen asuult hariultiig hadgalna.

    history = ai_chat_memory.get(user_id, [])

    history.append(("user", user_prompt))
    history.append(("assistant", assistant_answer))

    max_messages = AI_MAX_HISTORY_TURNS * 2

    ai_chat_memory[user_id] = history[-max_messages:]


async def ask_gemini(prompt: str, user_id: int) -> str:
    # Ene function n Gemini API ruu prompt yvuulj hariu avna.
    # General assistant maygaar hariulna.
    # Command execute hiihgui, zovhon text hariu butsaana.

    if gemini_ai is None:
        return "Gemini API key алга байна. .env дотор GEMINI_API_KEY нэм."

    clean_prompt = prompt.strip()

    if not clean_prompt:
        return "Prompt хоосон байна."

    try:
        conversation_text = build_ai_conversation_text(user_id, clean_prompt)

        response = await gemini_ai.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=conversation_text,
            config=types.GenerateContentConfig(
                system_instruction=build_agent_system_prompt(),
                temperature=0.7,
                max_output_tokens=2500,
            ),
        )

        answer = response.text

        if not answer:
            return "Gemini хоосон хариу буцаалаа."

        final_answer = answer.strip()

        remember_ai_chat(user_id, clean_prompt, final_answer)

        return final_answer

    except Exception as error:
        return f"Gemini API дээр алдаа гарлаа: {error}"


def is_git_repo(path: Path) -> bool:
    # Folder dotroo .git folder-toi bol git repo gej uzne.

    return path.is_dir() and (path / ".git").exists()


def list_git_repos() -> list[Path]:
    # PROJECTS_DIR dotorh buh git repo folder-uudiig jagsaana.
    # Zovhon shuud dotroh folder-uudiig shalgaj baina.

    if not PROJECTS_DIR.exists():
        return []

    repos = []

    for item in PROJECTS_DIR.iterdir():
        if is_git_repo(item):
            repos.append(item)

    return sorted(repos, key=lambda p: p.name.lower())


def safe_repo_path(repo_name: str) -> Path | None:
    # User-aas irsen repo ner ayulgui esehiig shalgana.
    # ../ geh meteer PROJECTS_DIR-s gadagsh garah oroldlogiig haana.

    repo_path = (PROJECTS_DIR / repo_name).resolve()

    try:
        repo_path.relative_to(PROJECTS_DIR)
    except ValueError:
        return None

    if not is_git_repo(repo_path):
        return None

    return repo_path


def protect_local_secrets(repo_path: Path) -> None:
    # Repo buriin .git/info/exclude dotor local nuuts file-uudiig oruulna.
    # Ene ni .env, .venv geh met file-uudiig git add -A hiih ued hamgaalna.

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
    # Odoogiin git branch-iin neriig avna.
    # Detached HEAD baival push hiih branch todorhoigui tul None butsaana.

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
    # Repo-d origin remote baigaa esehiig shalgana.
    # Origin baihgui bol push hiih bolomjgui.

    code, _ = await run_command(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_path,
        timeout=30,
    )

    return code == 0


async def has_upstream(repo_path: Path) -> bool:
    # Current branch upstream-toi esehiig shalgana.
    # Upstream baiwal simple git push ajillana.

    code, _ = await run_command(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=repo_path,
        timeout=30,
    )

    return code == 0


async def repo_status_text(repo_path: Path) -> str:
    # Repo dotroh oorchlogdson file-uudiig shalgaj text bolgoj butsaana.

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
    # Repo remote SSH ashiglaj baina uu HTTPS ashiglaj baina uu gedgiig shalgana.
    # HTTPS remote bol bot password asuugaad push fail hiih magadlal ondor.

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
    # Neg repo deer git add, commit, push hiine.
    # Ehnii alham deer local secret file-uudiig exclude hiij hamgaalna.

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
        return f"{repo_path.name}: committed, but branch is detached or unknown. Push manually."

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
    # Bot asah ued songoson channel ruu medegdel yvuulna.
    # Channel id baihgui bol message yvuulahgui.

    if not STARTUP_CHANNEL_ID:
        print("STARTUP_CHANNEL_ID is missing. Startup message skipped.")
        return

    try:
        channel_id = int(STARTUP_CHANNEL_ID)

        channel = client.get_channel(channel_id)

        if channel is None:
            channel = await client.fetch_channel(channel_id)

        startup_text = (
            "Local Discord Bot aslaa\n"
            f"Host: {platform.node()}\n"
            f"Projects: {PROJECTS_DIR}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        await channel.send(startup_text)

        print(f"Startup message sent to channel {channel_id}")

    except Exception as error:
        print(f"Failed to send startup message: {error}")


@client.event
async def on_ready():
    # Bot Discord deer amjilttai online boloh ued ajillana.
    # Startup message-iig neg l udaa yvuulna.

    print(f"Logged in as {client.user}")
    print(f"PROJECTS_DIR = {PROJECTS_DIR}")

    if not client.startup_message_sent:
        client.startup_message_sent = True
        await send_startup_message()


@client.tree.command(name="ping", description="Check whether the bot is online")
@owner_only()
async def ping(interaction: discord.Interaction):
    # Bot amid ajillaj baina uu gedgiig shalgah hamgiin engiin command.

    await interaction.response.send_message("pong")


@client.tree.command(name="pc", description="Show laptop status")
@owner_only()
async def pc(interaction: discord.Interaction):
    # Laptop-iin undsen status-g haruulna.
    # Uptime, CPU, RAM, disk, project folder geh medeelel yvuulna.

    uptime_seconds = int(time.time() - psutil.boot_time())
    uptime_hours = uptime_seconds // 3600
    uptime_minutes = (uptime_seconds % 3600) // 60

    cpu = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    message = (
        f"Host: {platform.node()}\n"
        f"OS: {platform.system()} {platform.release()}\n"
        f"Uptime: {uptime_hours}h {uptime_minutes}m\n"
        f"CPU: {cpu}%\n"
        f"RAM: {memory.percent}% used\n"
        f"Disk: {disk.percent}% used\n"
        f"Projects dir: {PROJECTS_DIR}"
    )

    await interaction.response.send_message(message)


@client.tree.command(name="projects", description="List folders inside Tengis directory")
@owner_only()
async def projects(interaction: discord.Interaction):
    # PROJECTS_DIR dotorh folder-uudiig jagsaana.
    # Ene ni repo bish engiin folder-uudiig ch haruulna.

    if not PROJECTS_DIR.exists():
        await interaction.response.send_message(f"{PROJECTS_DIR} folder not found.")
        return

    folders = sorted([p.name for p in PROJECTS_DIR.iterdir() if p.is_dir()])[:50]

    if not folders:
        await interaction.response.send_message("No folders found.")
        return

    await interaction.response.send_message("\n".join(folders))


@client.tree.command(name="repos", description="List git repositories inside Tengis directory")
@owner_only()
async def repos(interaction: discord.Interaction):
    # PROJECTS_DIR dotorh git repo-uudiig jagsaana.

    git_repos = list_git_repos()

    if not git_repos:
        await interaction.response.send_message("No git repositories found.")
        return

    names = [repo.name for repo in git_repos]

    await interaction.response.send_message("\n".join(names))


@client.tree.command(name="repo_status", description="Show status for one repo or all repos")
@app_commands.describe(repo_name="Optional: repo folder name. Leave empty to check all repos.")
@owner_only()
async def repo_status(interaction: discord.Interaction, repo_name: str = ""):
    # Neg repo esvel buh repo deer git status shalgana.
    # Urt ajillaj magadgui tul defer hiij Discord timeout-oos hamgaalna.

    await interaction.response.defer(thinking=True)

    if repo_name.strip():
        repo_path = safe_repo_path(repo_name.strip())

        if repo_path is None:
            await interaction.followup.send(
                f"{repo_name} is not a git repository inside {PROJECTS_DIR}."
            )
            return

        result = await repo_status_text(repo_path)
        await interaction.followup.send(result)
        return

    git_repos = list_git_repos()

    if not git_repos:
        await interaction.followup.send("No git repositories found.")
        return

    results = []

    for repo in git_repos:
        results.append(await repo_status_text(repo))

    final_message = "\n".join(results)

    await interaction.followup.send(truncate_message(final_message))


@client.tree.command(name="repo_remote", description="Check whether repos use SSH or HTTPS remote")
@app_commands.describe(repo_name="Optional: repo folder name. Leave empty to check all repos.")
@owner_only()
async def repo_remote(interaction: discord.Interaction, repo_name: str = ""):
    # Repo remote SSH uu HTTPS uu gedgiig shalgana.
    # Bot push hiih ued SSH remote hamgiin zov.

    await interaction.response.defer(thinking=True)

    if repo_name.strip():
        repo_path = safe_repo_path(repo_name.strip())

        if repo_path is None:
            await interaction.followup.send(
                f"{repo_name} is not a git repository inside {PROJECTS_DIR}."
            )
            return

        result = await remote_info_text(repo_path)
        await interaction.followup.send(result)
        return

    git_repos = list_git_repos()

    if not git_repos:
        await interaction.followup.send("No git repositories found.")
        return

    results = []

    for repo in git_repos:
        results.append(await remote_info_text(repo))

    final_message = "\n".join(results)

    await interaction.followup.send(truncate_message(final_message))


@client.tree.command(name="update_repo", description="Commit and push one repo or all repos")
@app_commands.describe(
    repo_name="Optional: repo folder name. Leave empty to update all repos.",
    message="Optional commit message.",
)
@owner_only()
async def update_repo(
    interaction: discord.Interaction,
    repo_name: str = "",
    message: str = DEFAULT_COMMIT_MESSAGE,
):
    # Neg repo esvel buh repo deer git add, commit, push hiine.
    # Ene command n laptop deerh code-iig GitHub ruu ilgeeh gol command.

    await interaction.response.defer(thinking=True)

    commit_message = message.strip() or DEFAULT_COMMIT_MESSAGE

    if repo_name.strip():
        repo_path = safe_repo_path(repo_name.strip())

        if repo_path is None:
            await interaction.followup.send(
                f"{repo_name} is not a git repository inside {PROJECTS_DIR}."
            )
            return

        result = await update_one_repo(repo_path, commit_message)

        await interaction.followup.send(truncate_message(result))
        return

    git_repos = list_git_repos()

    if not git_repos:
        await interaction.followup.send("No git repositories found.")
        return

    results = []

    for repo_path in git_repos:
        results.append(await update_one_repo(repo_path, commit_message))

    final_message = "\n".join(results)

    await interaction.followup.send(truncate_message(final_message))


@client.tree.command(name="startup_test", description="Send startup message manually for testing")
@owner_only()
async def startup_test(interaction: discord.Interaction):
    # Startup message-iig garaar test hiih command.
    # Bot asahgui baisan ch channel permission zov esehiig shalgahad heregtei.

    await interaction.response.defer(thinking=True)

    await send_startup_message()

    await interaction.followup.send("Startup message test done.")


@client.tree.command(name="ask", description="Ask Gemini AI assistant")
@app_commands.describe(prompt="Gemini-d asuuh asuult esvel daalgavar.")
@owner_only()
async def ask(interaction: discord.Interaction, prompt: str):
    # Ene command n Discord-oos Gemini AI assistant ruu prompt yvuulna.
    # Zovhon owner ashiglana.
    # Hariu udaj magadgui tul defer ashiglana.

    await interaction.response.defer(thinking=True)

    answer = await ask_gemini(prompt, interaction.user.id)

    await interaction.followup.send(truncate_message(answer))


@client.tree.command(name="ai_model", description="Show current Gemini model")
@owner_only()
async def ai_model(interaction: discord.Interaction):
    # Odoo ashiglaj baigaa Gemini model-iig haruulna.
    # Model-iig solihdoo .env dotor GEMINI_MODEL shinechleed bot restart hiine.
    # Mun AI history heden turn hadgalj baigaag haruulna.

    await interaction.response.send_message(
        f"Current Gemini model: {GEMINI_MODEL}\nAI history turns: {AI_MAX_HISTORY_TURNS}"
    )


@client.tree.command(name="ai_reset", description="Reset AI chat memory")
@owner_only()
async def ai_reset(interaction: discord.Interaction):
    # Ene command n AI chat history-g tseverlene.
    # Gemini umnuh context-oos bolj buruu oilgood baival ashiglana.

    ai_chat_memory.pop(interaction.user.id, None)

    await interaction.response.send_message("AI chat memory цэвэрлэгдлээ.", ephemeral=True)


@client.tree.command(name="shutdown", description="Shutdown the laptop")
@app_commands.describe(
    minutes="Shutdown hiih hurtel huleeh minut. 0 bol 3 second huleene.",
    confirm="yes gej bichvel shutdown ajillana.",
)
@owner_only()
async def shutdown(
    interaction: discord.Interaction,
    minutes: app_commands.Range[int, 0, MAX_SHUTDOWN_MINUTES] = 1,
    confirm: str = "no",
):
    # Laptop-iig Discord-oos shutdown hiih command.
    # Confirm yes baihgui bol shutdown ajillahgui.
    # Ene ni sanamsargui command ajilluulahaas hamgaalna.

    global pending_shutdown_task

    if confirm.lower().strip() not in ["yes", "y", "tiim", "tiimee"]:
        await interaction.response.send_message(
            "Shutdown hiih bol confirm deer yes gej bich.",
            ephemeral=True,
        )
        return

    if pending_shutdown_task and not pending_shutdown_task.done():
        pending_shutdown_task.cancel()

    await interaction.response.send_message(
        f"Laptop {minutes} minut daraa shutdown hiine. Tsutslah bol /cancel_shutdown ashigla.",
        ephemeral=True,
    )

    async def do_shutdown():
        # Ene dotood task n songoson hugatsaag huleegeed shutdown command ajilluulna.
        # minutes 0 bol shuud bish 3 second huleene.

        try:
            if minutes > 0:
                await asyncio.sleep(minutes * 60)
            else:
                await asyncio.sleep(3)

            subprocess.run(
                POWER_OFF_COMMAND,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
            )

        except asyncio.CancelledError:
            return

        except Exception as error:
            print(f"Shutdown failed: {error}")

            try:
                await interaction.followup.send(
                    f"Shutdown hiij chadsangui: {error}",
                    ephemeral=True,
                )
            except Exception as followup_error:
                print(f"Failed to send shutdown error followup: {followup_error}")

    pending_shutdown_task = asyncio.create_task(do_shutdown())


@client.tree.command(name="cancel_shutdown", description="Cancel scheduled shutdown")
@owner_only()
async def cancel_shutdown(interaction: discord.Interaction):
    # Omno schedule hiisen shutdown baival tsutsalna.
    # Python task ajillaj baival cancel hiine.

    global pending_shutdown_task

    if pending_shutdown_task and not pending_shutdown_task.done():
        pending_shutdown_task.cancel()
        await interaction.response.send_message(
            "Shutdown tsutslagdsan.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        "Odoogoor schedule hiisen shutdown alga.",
        ephemeral=True,
    )


# Bot asahaas omno token baigaa esehiig shalgana.
# Token baihgui bol Discord ruu holbogdoh bolomjgui.
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add DISCORD_TOKEN to .env")


# Bot-iig ajilluulj baina.
# Ene muriin daraa process zogsoh hurtel bot online baina.
client.run(DISCORD_TOKEN)