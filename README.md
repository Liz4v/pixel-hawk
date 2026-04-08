# Pixel Hawk

A Discord bot that watches over your artwork on [WPlace](https://wplace.live/). It polls the canvas, compares it to your project images, and tells you how your art is doing: completion percentage, progress, and who's griefing you.

Multiple people in your server can track their own projects at the same time.

## Getting started

You'll need a computer (or server) that can stay online while the bot runs. A cheap Linux VPS, a Raspberry Pi, or just your desktop works fine.

### 1. Install uv

[uv](https://docs.astral.sh/uv/) is a fast Python package manager. Install it by following the instructions on the [uv installation page](https://docs.astral.sh/uv/getting-started/installation/).

### 2. Install Pixel Hawk

Open a terminal and run:

```bash
uv tool install git+https://github.com/Liz4v/pixel-hawk.git
```

This installs the `hawk` command on your system. You can update later with:

```bash
uv tool upgrade pixel-hawk
```

### 3. Create a Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application**, give it a name (like "Pixel Hawk"), and create it
3. Give it a pretty profile picture, too 🖼️🎨🖌️😊
4. Go to the **Bot** tab on the left
5. Click **Reset Token**, then **Copy**; save this token somewhere safe, you'll need it in a moment

### 4. Add the bot to your server

1. Still in the Developer Portal, go to the **OAuth2** tab
2. Under **OAuth2 URL Generator**, check the **bot** scope
3. Under **Bot Permissions**, check:
   - **Send Messages**
   - **Use Slash Commands**
4. Copy the generated URL at the bottom and open it in your browser
5. Select your server and authorize the bot

### 5. Configure and run

Create a folder where Pixel Hawk will store its data, and add a `.env` file with your bot token:

```bash
mkdir pixel-hawk && cd pixel-hawk
echo "HAWK_BOT_TOKEN=paste-your-token-here" > .env
```

Then start the bot:

```bash
uv run hawk
```

That's it! Pixel Hawk will initialize its database, connect to Discord, and start syncing its slash commands. Give it a few seconds to appear online in your server.

### 6. Make yourself an admin

In your Discord server, type:

```
/hawkadmin admin user:@yourself
```

Pick your own name from the user selector. This only works for the first admin; it makes you the first (and for now, only) admin.

### 7. Set the required role

Choose which role in your server gives people access to the bot:

```
/hawkadmin role name:Artist
```

Replace `Artist` with whatever role you want. Anyone with that role can now use `/hawk` commands. People without the role won't see anything. You can change this anytime.

## Using the bot

### Creating a project

1. Make your art as a PNG image using the WPlace palette
2. Run `/hawk new` and upload the image; give it a name and the WPlace coordinates where it should go
3. Pixel Hawk starts polling the canvas and tracking your art

### Watching a project

Run `/hawk watch` and pick your project. Pixel Hawk posts a message in the channel that auto-updates every time it detects changes, showing completion percentage, pixel counts, progress rate, ETA, and 24-hour activity.

### All commands

**User commands** (`/hawk`):

| Command | What it does |
|---|---|
| `/hawk new` | Upload a new project image to track |
| `/hawk edit` | Change a project's name, coordinates, state, or image |
| `/hawk delete` | Permanently delete a project |
| `/hawk list` | See all your projects with stats (only visible to you) |
| `/hawk watch` | Post a live-updating status message for a project |
| `/hawk unwatch` | Stop watching a project in this channel |

All user commands also work in DMs, as long as you've used the bot in a server at least once before.

**Admin commands** (`/hawkadmin`, server only):

| Command | What it does |
|---|---|
| `/hawkadmin admin` | Grant bot admin to a user |
| `/hawkadmin role` | Set the required role for this server |
| `/hawkadmin quota` | View or set per-user project/tile limits |
| `/hawkadmin guildquota` | View or set server-wide quota ceilings |

## Features

- **Live progress tracking**: watches your art on the canvas and reports completion percentage, pixel-by-pixel
- **Auto-updating Discord messages**: `/hawk watch` posts a message that refreshes with current stats every polling cycle
- **Grief detection**: spots when someone paints over your art and tells you who did it
- **Multi-user support**: everyone in the server can track their own projects independently
- **Smart polling**: prioritizes tiles that change often, so active areas get checked more frequently
- **Per-user and per-server quotas**: admins control how many projects and tiles each person (and the whole server) can track
- **Project states**: set projects to active, passive (piggybacks on other polls), or inactive to manage resource usage
- **DM support**: use commands in DMs after your first interaction in a server

## Advanced

### Configuration

| Setting | Default | How to set it |
|---|---|---|
| Bot token | *(required)* | `HAWK_BOT_TOKEN` in `.env` |
| Command prefix | `hawk` | `HAWK_COMMAND_PREFIX` in `.env` |
| Data directory | `./nest` | `HAWK_NEST` in `.env`, or `hawk --nest /path` |

### Running as a service (Linux)

For a server that stays online without a terminal open:

```bash
git clone https://github.com/Liz4v/pixel-hawk.git ~/pixel-hawk
cd ~/pixel-hawk
bash scripts/install-service.sh
sudo nano /etc/pixel-hawk.env   # set HAWK_BOT_TOKEN here
sudo systemctl restart pixel-hawk
```

### Development

```bash
git clone https://github.com/Liz4v/pixel-hawk.git
cd pixel-hawk
uv sync
uv run hawk
```

Linting, type checking, and tests:

```bash
uv run ruff check
uv run ty check
uv run pytest
```

## Other tools that motivated me to build this

- [Blue Marble](https://bluemarble.lol/)
- [WPlace Art Exporter](https://gist.github.com/Kottonye/0e460154cb9b3132c940fa2b4be52faf)
- [Skirk Marble](https://github.com/Seris0/Wplace-SkirkMarble)
- [TWY's Blue Marble](https://github.com/t-wy/Wplace-BlueMarble-Userscripts)
