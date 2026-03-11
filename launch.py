import json
import os
import random
import re
import shlex
import signal
import subprocess
import sys
from pathlib import Path

signal.signal(signal.SIGTERM, signal.default_int_handler)

CONFIG_GENERATED = "/reforger/Configs/docker_generated.json"
SENTINEL_WINDOWS_FIX = "/reforger/.windows_fix_done"


def env(key, default=""):
    return os.environ.get(key, default)


def env_defined(key):
    return bool(os.environ.get(key))


def bool_str(text):
    return text.lower() == "true"


def random_passphrase():
    with open("/usr/share/dict/american-english") as f:
        words = [w.strip().lower() for w in f if "'" not in w]
    return "-".join(random.sample(words, 2))


def build_steamcmd_args(platform=None):
    """Build steamcmd argument list for server install/update."""
    args = ["/steamcmd/steamcmd.sh", "+force_install_dir", "/reforger"]
    if env_defined("STEAM_USER"):
        args.extend(["+login", env("STEAM_USER"), env("STEAM_PASSWORD")])
    else:
        args.extend(["+login", "anonymous"])
    if platform:
        args.extend(["+@sSteamCmdForcePlatformType", platform])
    args.extend(["+app_update", env("STEAM_APPID")])
    if env_defined("STEAM_BRANCH"):
        args.extend(["-beta", env("STEAM_BRANCH")])
    if env_defined("STEAM_BRANCH_PASSWORD"):
        args.extend(["-betapassword", env("STEAM_BRANCH_PASSWORD")])
    args.extend(["validate", "+quit"])
    return args


def install_server():
    """Download/update the server via steamcmd."""
    if env("SKIP_INSTALL", "false").lower() in ("true", "1"):
        return

    app_id = env("STEAM_APPID")
    sentinel = Path(SENTINEL_WINDOWS_FIX)

    # Warm-up login so steamcmd caches its configuration
    subprocess.call(["/steamcmd/steamcmd.sh", "+login", "anonymous", "+quit"])

    if app_id == "1890870":
        # Experimental appId needs a one-time Windows platform pass
        if not sentinel.exists():
            subprocess.call(build_steamcmd_args(platform="windows"))
            sentinel.touch()
        subprocess.call(build_steamcmd_args(platform="linux"))
    else:
        if sentinel.exists():
            sentinel.unlink()
        subprocess.call(build_steamcmd_args())


def load_config():
    """Load existing generated config, or fall back to defaults."""
    path = CONFIG_GENERATED if os.path.exists(CONFIG_GENERATED) else "/docker_default.json"
    with open(path) as f:
        return json.load(f)


def parse_mods(game):
    """Build the mod list from GAME_MODS_IDS_LIST and GAME_MODS_JSON_FILE_PATH."""
    game["mods"] = []
    seen_ids = set()

    if env_defined("GAME_MODS_IDS_LIST"):
        mods_str = env("GAME_MODS_IDS_LIST")
        if not re.fullmatch(r"[A-Z\d,=.]+", mods_str):
            raise ValueError("Illegal characters in GAME_MODS_IDS_LIST")
        version_re = re.compile(r"^\d+\.\d+\.\d+$")
        for entry in filter(None, mods_str.split(",")):
            parts = entry.split("=")
            if len(parts) not in (1, 2):
                raise ValueError(f"Mod '{entry}' not defined properly")
            mod_id = parts[0]
            if mod_id in seen_ids:
                continue
            mod = {"modId": mod_id}
            if len(parts) == 2:
                if not version_re.match(parts[1]):
                    raise ValueError(f"Mod '{entry}' version doesn't match X.Y.Z")
                mod["version"] = parts[1]
            seen_ids.add(mod_id)
            game["mods"].append(mod)

    if env_defined("GAME_MODS_JSON_FILE_PATH"):
        with open(env("GAME_MODS_JSON_FILE_PATH")) as f:
            json_mods = json.load(f)
        allowed_keys = {"modId", "name", "version"}
        for entry in json_mods:
            if "modId" not in entry:
                raise ValueError(f"Mod entry missing modId: {entry}")
            if entry["modId"] in seen_ids:
                continue
            seen_ids.add(entry["modId"])
            game["mods"].append({k: v for k, v in entry.items() if k in allowed_keys})


def apply_env_to_config(config):
    """Overwrite config values from environment variables."""
    # Network
    if env_defined("SERVER_BIND_ADDRESS"):
        config["bindAddress"] = env("SERVER_BIND_ADDRESS")
    if env_defined("SERVER_BIND_PORT"):
        config["bindPort"] = int(env("SERVER_BIND_PORT"))
    if env_defined("SERVER_PUBLIC_ADDRESS"):
        config["publicAddress"] = env("SERVER_PUBLIC_ADDRESS")
    if env_defined("SERVER_PUBLIC_PORT"):
        config["publicPort"] = int(env("SERVER_PUBLIC_PORT"))

    # A2S
    if env_defined("SERVER_A2S_ADDRESS") and env_defined("SERVER_A2S_PORT"):
        config["a2s"] = {
            "address": env("SERVER_A2S_ADDRESS"),
            "port": int(env("SERVER_A2S_PORT")),
        }
    else:
        config["a2s"] = None

    # RCON
    if env_defined("RCON_ADDRESS") and env_defined("RCON_PORT"):
        config["rcon"] = {
            "address": env("RCON_ADDRESS"),
            "port": int(env("RCON_PORT")),
            "password": env("RCON_PASSWORD"),
            "permission": env("RCON_PERMISSION"),
        }
    else:
        config["rcon"] = None

    # Game settings
    game = config["game"]
    if env_defined("GAME_NAME"):
        game["name"] = env("GAME_NAME")
    if env_defined("GAME_PASSWORD"):
        game["password"] = env("GAME_PASSWORD")
    if env_defined("GAME_PASSWORD_ADMIN"):
        game["passwordAdmin"] = env("GAME_PASSWORD_ADMIN")
    else:
        password = random_passphrase()
        game["passwordAdmin"] = password
        print(f"Admin password: {password}")
    if env_defined("GAME_ADMINS"):
        game["admins"] = [a for a in env("GAME_ADMINS").split(",") if a]
    if env_defined("GAME_SCENARIO_ID"):
        game["scenarioId"] = env("GAME_SCENARIO_ID")
    if env_defined("GAME_MAX_PLAYERS"):
        game["maxPlayers"] = int(env("GAME_MAX_PLAYERS"))
    if env_defined("GAME_VISIBLE"):
        game["visible"] = bool_str(env("GAME_VISIBLE"))
    if env_defined("GAME_SUPPORTED_PLATFORMS"):
        game["supportedPlatforms"] = env("GAME_SUPPORTED_PLATFORMS").split(",")

    # Game properties — map env vars to config keys with their type converters
    props = game["gameProperties"]
    prop_map = {
        "GAME_PROPS_BATTLEYE": ("battlEye", bool_str),
        "GAME_PROPS_DISABLE_THIRD_PERSON": ("disableThirdPerson", bool_str),
        "GAME_PROPS_FAST_VALIDATION": ("fastValidation", bool_str),
        "GAME_PROPS_SERVER_MAX_VIEW_DISTANCE": ("serverMaxViewDistance", int),
        "GAME_PROPS_SERVER_MIN_GRASS_DISTANCE": ("serverMinGrassDistance", int),
        "GAME_PROPS_NETWORK_VIEW_DISTANCE": ("networkViewDistance", int),
        "GAME_PROPS_VON_DISABLE_UI": ("VONDisableUI", bool_str),
        "GAME_PROPS_VON_DISABLE_DIRECT_SPEECH_UI": ("VONDisableDirectSpeechUI", bool_str),
        "GAME_PROPS_VON_CAN_TRANSMIT_CROSS_FACTION": ("VONCanTransmitCrossFaction", bool_str),
    }
    for env_key, (config_key, convert) in prop_map.items():
        if env_defined(env_key):
            props[config_key] = convert(env(env_key))

    # Mods (always regenerated from env to keep it as single source of truth)
    parse_mods(game)


def generate_config():
    """Generate server config from env vars and return the config file path."""
    if env("ARMA_CONFIG") != "docker_generated":
        return f"/reforger/Configs/{env('ARMA_CONFIG')}"

    config = load_config()
    apply_env_to_config(config)

    with open(CONFIG_GENERATED, "w") as f:
        json.dump(config, f, indent=4)

    return CONFIG_GENERATED


def main():
    install_server()
    config_path = generate_config()

    launch = [
        env("ARMA_BINARY"),
        "-config", config_path,
        "-backendlog",
        "-nothrow",
        "-maxFPS", env("ARMA_MAX_FPS"),
        "-profile", env("ARMA_PROFILE"),
        "-addonDownloadDir", env("ARMA_WORKSHOP_DIR"),
        "-addonsDir", env("ARMA_WORKSHOP_DIR"),
        *shlex.split(env("ARMA_PARAMS")),
    ]

    print(shlex.join(launch), flush=True)

    binary = Path(env("ARMA_BINARY"))
    if not binary.exists():
        print(f"ERROR: Server binary not found: {binary}", flush=True)
        print("steamcmd may have failed to install. Check logs above.", flush=True)
        sys.exit(1)

    proc = subprocess.Popen(launch)
    try:
        try:
            sys.exit(proc.wait())
        except KeyboardInterrupt:
            proc.send_signal(signal.SIGINT)
            sys.exit(proc.wait())
    except SystemExit:
        raise
    except BaseException:
        proc.kill()
        raise


if __name__ == "__main__":
    main()
