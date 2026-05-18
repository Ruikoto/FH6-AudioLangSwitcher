#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FH6Lang - 极限竞速：地平线 6 中文 UI + 日语配音切换工具

通过互换 ./media/Stripped/StringTables/CHS.zip 与 JP.zip 这两个语言包文件，
并在 AppData 下写入 UserPreferredLang=JP，让游戏以中文界面 + 日语配音运行。

仅依赖 Python 标准库。支持 Windows (Steam / Xbox) 和 Linux Steam Deck (Steam)。
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

# 游戏识别字串集中放这里，命名如有变动改这一组即可
GAME_DISPLAY_NAME = "极限竞速：地平线 6 (Forza Horizon 6)"
GAME_STEAM_APPID = "2483190"
GAME_STEAM_NAME_PATTERN = re.compile(r"forza\s*horizon\s*6", re.IGNORECASE)
GAME_INSTALLDIR_HINTS = ("ForzaHorizon6", "Forza Horizon 6")
GAME_EXE_PATTERN = re.compile(r"^ForzaHorizon6.*\.exe$", re.IGNORECASE)
GAME_EXE_EXCLUDES = {
    "ForzaHorizon6Launcher.exe",
    "EasyAntiCheat.exe",
    "EasyAntiCheat_EOS.exe",
    "gamelaunchhelper.exe",
}

STRINGTABLES_REL = Path("media") / "Stripped" / "StringTables"
LANG_FILES = ("CHS.zip", "JP.zip")
USER_PREF_DIR = "ForzaHorizon6"
USER_PREF_FILE = "UserPreferredLang"
USER_PREF_VALUE = "JP"

APP_NAME = "FH6Lang"


# ---------------------------------------------------------------------------
# 终端着色
# ---------------------------------------------------------------------------

def _enable_vt_on_windows() -> bool:
    if not IS_WINDOWS:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        if handle in (0, -1):
            return False
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _force_console_utf8() -> None:
    # 默认 Windows cmd 是 GBK，print 到带 ✓✗⚠ 的字符串会 UnicodeEncodeError 崩
    if IS_WINDOWS:
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


_force_console_utf8()
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and _enable_vt_on_windows()


def _wrap(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def red(s: str) -> str: return _wrap(s, "31")
def green(s: str) -> str: return _wrap(s, "32")
def yellow(s: str) -> str: return _wrap(s, "33")
def cyan(s: str) -> str: return _wrap(s, "36")
def bold(s: str) -> str: return _wrap(s, "1")


def info(msg: str) -> None: print(msg)
def ok(msg: str) -> None: print(green("  ✓ ") + msg)
def warn(msg: str) -> None: print(yellow("  ⚠ ") + msg)
def fail(msg: str) -> None: print(red("  ✗ ") + msg)


# ---------------------------------------------------------------------------
# Windows UAC 提权
# ---------------------------------------------------------------------------

def is_admin_windows() -> bool:
    if not IS_WINDOWS:
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin_windows() -> bool:
    """在 Windows 下以管理员身份重启自己。成功后返回 True，调用方应退出。"""
    if not IS_WINDOWS or is_admin_windows():
        return False
    try:
        exe = sys.executable
        if getattr(sys, "frozen", False):
            argv = sys.argv[1:]
        else:
            argv = [sys.argv[0]] + sys.argv[1:]
        # list2cmdline 处理含空格 / 引号的参数，比手工拼引号稳
        params = subprocess.list2cmdline(argv)
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return int(rc) > 32
    except Exception as e:
        warn(f"UAC 提权失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 极简 VDF / ACF 解析器（Valve KeyValues v1）
# ---------------------------------------------------------------------------

def parse_vdf(text: str) -> dict:
    pos = 0
    n = len(text)

    def skip_ws():
        nonlocal pos
        while pos < n:
            ch = text[pos]
            if ch in " \t\r\n":
                pos += 1
            elif text[pos:pos + 2] == "//":
                while pos < n and text[pos] != "\n":
                    pos += 1
            else:
                break

    def read_str():
        nonlocal pos
        if pos >= n or text[pos] != '"':
            raise ValueError(f"VDF: 期望字符串于位置 {pos}")
        pos += 1
        out = []
        while pos < n:
            ch = text[pos]
            if ch == "\\" and pos + 1 < n:
                nxt = text[pos + 1]
                out.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(nxt, nxt))
                pos += 2
            elif ch == '"':
                pos += 1
                return "".join(out)
            else:
                out.append(ch)
                pos += 1
        raise ValueError("VDF: 字符串未结束")

    def read_obj():
        nonlocal pos
        obj = {}
        skip_ws()
        if pos < n and text[pos] == "{":
            pos += 1
        while True:
            skip_ws()
            if pos >= n:
                break
            if text[pos] == "}":
                pos += 1
                break
            key = read_str()
            skip_ws()
            if pos < n and text[pos] == "{":
                obj[key] = read_obj()
            else:
                obj[key] = read_str()
        return obj

    root = {}
    skip_ws()
    while pos < n:
        skip_ws()
        if pos >= n or text[pos] != '"':
            break
        key = read_str()
        skip_ws()
        if pos < n and text[pos] == "{":
            root[key] = read_obj()
        else:
            root[key] = read_str()
    return root


def _read_text_loose(path: Path) -> str:
    # Steam 的 VDF/ACF 一般是 UTF-8，但兼容下 latin-1 兜底
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_bytes().decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# Steam 路径检测
# ---------------------------------------------------------------------------

def find_steam_root() -> list[Path]:
    """返回所有候选 Steam 根目录（按可能性排序）。"""
    candidates: list[Path] = []

    if IS_WINDOWS:
        try:
            import winreg
            for hive, sub, val in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
            ):
                try:
                    with winreg.OpenKey(hive, sub) as key:
                        path, _ = winreg.QueryValueEx(key, val)
                        p = Path(path)
                        if p.exists() and p not in candidates:
                            candidates.append(p)
                except OSError:
                    pass
        except ImportError:
            pass
        for p in (Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")):
            if p.exists() and p not in candidates:
                candidates.append(p)
    elif IS_LINUX:
        home = Path.home()
        for rel in (
            ".steam/steam",
            ".local/share/Steam",
            ".steam/debian-installation",
            ".var/app/com.valvesoftware.Steam/data/Steam",
        ):
            p = home / rel
            try:
                p = p.resolve()
            except OSError:
                continue
            if p.exists() and p not in candidates:
                candidates.append(p)

    return candidates


def find_steam_libraries(steam_root: Path) -> list[Path]:
    """从 libraryfolders.vdf 枚举所有库目录。"""
    libs: list[Path] = []
    vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
    if vdf_path.exists():
        try:
            data = parse_vdf(_read_text_loose(vdf_path))
            folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
            if isinstance(folders, dict):
                for k, v in folders.items():
                    if not k.isdigit():
                        continue
                    path_str = v.get("path") if isinstance(v, dict) else v
                    if not path_str:
                        continue
                    p = Path(path_str)
                    if p.exists() and p not in libs:
                        libs.append(p)
        except Exception as e:
            warn(f"解析 libraryfolders.vdf 失败: {e}")
    # 默认库就是 steam_root 自己
    if steam_root.exists() and steam_root not in libs:
        libs.insert(0, steam_root)
    return libs


def _read_acf_install(acf: Path) -> tuple[str, str, str] | None:
    """返回 (name, installdir, appid) 或 None。"""
    try:
        data = parse_vdf(_read_text_loose(acf))
    except Exception:
        return None
    app = data.get("AppState") or data.get("appstate")
    if not isinstance(app, dict):
        return None
    return app.get("name", ""), app.get("installdir", ""), app.get("appid", "")


def find_steam_game(steam_root: Path) -> tuple[Path, str] | None:
    """在所有 Steam 库里找 FH6。返回 (install_dir, appid) 或 None。"""
    libs = find_steam_libraries(steam_root)
    # 第一遍：按 AppID 直查 appmanifest_<id>.acf
    for lib in libs:
        acf = lib / "steamapps" / f"appmanifest_{GAME_STEAM_APPID}.acf"
        if not acf.exists():
            continue
        info_tuple = _read_acf_install(acf)
        if not info_tuple:
            continue
        _, installdir, appid = info_tuple
        if installdir:
            install_path = lib / "steamapps" / "common" / installdir
            if install_path.exists():
                return install_path, appid or GAME_STEAM_APPID
    # 第二遍：兜底按名字 / installdir 匹配（应对未来 AppID 变动 / 测试版等情况）
    for lib in libs:
        steamapps = lib / "steamapps"
        if not steamapps.exists():
            continue
        for acf in steamapps.glob("appmanifest_*.acf"):
            info_tuple = _read_acf_install(acf)
            if not info_tuple:
                continue
            name, installdir, appid = info_tuple
            if not (GAME_STEAM_NAME_PATTERN.search(name) or
                    any(h.lower() == installdir.lower() for h in GAME_INSTALLDIR_HINTS)):
                continue
            install_path = steamapps / "common" / installdir
            if install_path.exists():
                return install_path, appid
    return None


# ---------------------------------------------------------------------------
# Xbox 路径检测（仅 Windows）
# ---------------------------------------------------------------------------

def find_xbox_game() -> Path | None:
    if not IS_WINDOWS:
        return None
    # 1. 注册表枚举 Gaming Services
    try:
        import winreg
        root_key = r"SOFTWARE\Microsoft\GamingServices\PackageRepository\Root"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root_key) as rk:
            i = 0
            while True:
                try:
                    guid = winreg.EnumKey(rk, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(rk, guid) as gk:
                        j = 0
                        while True:
                            try:
                                idx = winreg.EnumKey(gk, j)
                            except OSError:
                                break
                            j += 1
                            try:
                                with winreg.OpenKey(gk, idx) as ek:
                                    root_val, _ = winreg.QueryValueEx(ek, "Root")
                                    p = Path(root_val)
                                    # Root 通常指向 .../Content/<exe> 或 .../Content
                                    # 向上找到含 media/Stripped/StringTables 的目录
                                    for candidate in (p, p.parent, p.parent.parent):
                                        if (candidate / STRINGTABLES_REL).exists():
                                            if GAME_STEAM_NAME_PATTERN.search(str(candidate)):
                                                return candidate
                            except OSError:
                                pass
                except OSError:
                    pass
    except (ImportError, OSError):
        pass

    # 2. 默认 XboxGames 目录直查 + .GamingRoot 探测
    drives = []
    try:
        import string
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if bitmask & (1 << i):
                drives.append(Path(f"{letter}:\\"))
    except Exception:
        drives = [Path(f"{d}:\\") for d in "CDEFGHIJ"]

    for drive in drives:
        # .GamingRoot 文件可能指向自定义 XboxGames 位置
        gaming_root = drive / ".GamingRoot"
        xbox_dirs = [drive / "XboxGames"]
        if gaming_root.exists():
            try:
                raw = gaming_root.read_bytes()
                # 跳过 4 字节 RGBX 头，剩下是 UTF-16LE 字符串
                if raw[:4] == b"RGBX" and len(raw) > 4:
                    txt = raw[4:].decode("utf-16-le", errors="ignore").rstrip("\x00")
                    if txt:
                        rel = Path(txt.replace("\\", "/").lstrip("/").lstrip(":"))
                        custom = drive / rel
                        if custom.exists() and custom not in xbox_dirs:
                            xbox_dirs.append(custom)
            except Exception:
                pass
        for xb in xbox_dirs:
            if not xb.exists():
                continue
            for sub in xb.iterdir():
                if not sub.is_dir():
                    continue
                if not GAME_STEAM_NAME_PATTERN.search(sub.name):
                    continue
                content = sub / "Content"
                if (content / STRINGTABLES_REL).exists():
                    return content
                if (sub / STRINGTABLES_REL).exists():
                    return sub
    return None


# ---------------------------------------------------------------------------
# 进程检测
# ---------------------------------------------------------------------------

def is_game_running() -> bool:
    try:
        if IS_WINDOWS:
            flags = 0x08000000  # CREATE_NO_WINDOW
            out = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10, creationflags=flags,
            )
            for line in out.stdout.splitlines():
                m = re.match(r'"([^"]+)"', line)
                if not m:
                    continue
                name = m.group(1)
                if name in GAME_EXE_EXCLUDES:
                    continue
                if GAME_EXE_PATTERN.match(name):
                    return True
        else:
            # Linux: pgrep -af 输出 "<pid> <cmdline>"，Proton 下 exe 名保留
            out = subprocess.run(
                ["pgrep", "-af", "ForzaHorizon6"],
                capture_output=True, text=True, timeout=10,
            )
            for line in out.stdout.splitlines():
                if any(ex in line for ex in GAME_EXE_EXCLUDES):
                    continue
                # 对 cmdline 里每个 token 取 basename 匹配（GAME_EXE_PATTERN 是锚定到完整 exe 名的）
                for token in line.split():
                    name = os.path.basename(token)
                    if name in GAME_EXE_EXCLUDES:
                        continue
                    if GAME_EXE_PATTERN.match(name):
                        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return False


# ---------------------------------------------------------------------------
# Hash 与状态文件
# ---------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _install_key(install_dir: Path) -> str:
    # Windows 不区分大小写，lower() 防止同一目录被记成两条状态
    s = str(install_dir.resolve())
    return s.lower() if IS_WINDOWS else s


def state_file_path() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / APP_NAME / "state.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "fh6lang" / "state.json"


def load_state() -> dict:
    p = state_file_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        warn(f"读取状态文件失败，忽略: {e}")
        return {}


def save_state(state: dict) -> None:
    p = state_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # tmp + replace 避免写到一半被中断留下半截 json
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# UserPreferredLang
# ---------------------------------------------------------------------------

def user_pref_path(version: str, steam_appid: str | None = None) -> Path | None:
    """返回该版本游戏对应的 UserPreferredLang 文件路径。"""
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / USER_PREF_DIR / USER_PREF_FILE
    # Linux: 只有 Steam 版能定位（要 Proton compatdata），manual 路径无从推断
    if version != "steam":
        return None
    appid = steam_appid or GAME_STEAM_APPID
    for root in find_steam_root():
        pfx = root / "steamapps" / "compatdata" / appid / "pfx"
        if pfx.exists():
            return (pfx / "drive_c" / "users" / "steamuser" /
                    "AppData" / "Local" / USER_PREF_DIR / USER_PREF_FILE)
    return None


def read_user_pref(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        # utf-8-sig 处理万一游戏写出带 BOM 的情况
        return path.read_bytes().decode("utf-8-sig", errors="replace").strip()
    except Exception:
        return None


def write_user_pref(path: Path, value: str = USER_PREF_VALUE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(value.encode("utf-8"))


# ---------------------------------------------------------------------------
# 互换 / 还原
# ---------------------------------------------------------------------------

class SwapError(Exception):
    pass


def swap_zips(stringtables_dir: Path) -> None:
    chs = stringtables_dir / LANG_FILES[0]
    jp = stringtables_dir / LANG_FILES[1]
    tmp = stringtables_dir / (LANG_FILES[0] + ".fh6lang.tmp")
    if tmp.exists():
        raise SwapError(f"临时文件已存在，可能上次互换未完成: {tmp}")
    # 三步原子化
    os.rename(chs, tmp)
    try:
        os.rename(jp, chs)
    except Exception:
        # 回滚步骤 1
        os.rename(tmp, chs)
        raise
    try:
        os.rename(tmp, jp)
    except Exception:
        # 回滚步骤 2 和 1
        os.rename(chs, jp)
        os.rename(tmp, chs)
        raise


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def banner() -> None:
    print()
    print(cyan("=" * 60))
    print(bold(cyan(f"  {APP_NAME} - {GAME_DISPLAY_NAME}")))
    print(cyan("  中文 UI + 日语配音 切换工具"))
    print(cyan("=" * 60))
    print()


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        ans = ""
    return ans or default


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        ans = ask(f"{prompt} [{d}]").lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


def prompt_version_choice() -> str:
    info(bold("请选择游戏版本："))
    info("  [1] Steam 版")
    info("  [2] Xbox / Microsoft Store / Game Pass 版" + (" (仅 Windows)" if not IS_WINDOWS else ""))
    info("  [3] 手动指定游戏目录")
    info("  [Q] 退出")
    while True:
        ans = ask("请输入选项").lower()
        if ans in ("1", "steam"):
            return "steam"
        if ans in ("2", "xbox"):
            if IS_WINDOWS:
                return "xbox"
            warn("Xbox / Game Pass 版仅在 Windows 上可用，请改选 1 或 3。")
            continue
        if ans in ("3", "manual"):
            return "manual"
        if ans in ("q", "quit", "exit"):
            sys.exit(0)
        warn("无效选项，请重新输入。")


def confirm_install_dir(candidate: Path | None) -> Path | None:
    if candidate is not None:
        info(f"检测到安装目录: {bold(str(candidate))}")
        if ask_yes_no("是否使用此目录？", default=True):
            return candidate
    while True:
        manual = ask("请粘贴游戏安装目录的完整路径（包含 media/Stripped/StringTables 的那一层）")
        if not manual:
            return None
        p = Path(manual).expanduser()
        if (p / STRINGTABLES_REL).exists():
            return p
        fail(f"该路径下没有找到 {STRINGTABLES_REL}/，请重新输入或留空退出。")


def validate_install(install_dir: Path) -> Path:
    st = install_dir / STRINGTABLES_REL
    if not st.exists():
        raise SwapError(f"找不到目录: {st}")
    missing = [f for f in LANG_FILES if not (st / f).exists()]
    if missing:
        raise SwapError(f"缺少语言包文件: {', '.join(missing)}")
    return st


def describe_state(install_key: str, stringtables: Path, pref_path: Path | None,
                   state: dict) -> tuple[str, dict]:
    """返回 ('original' | 'swapped' | 'unknown', 当前 hash 字典)。"""
    info("计算文件哈希...")
    current = {f: sha256_of(stringtables / f) for f in LANG_FILES}
    entry = state.get(install_key, {})
    orig = entry.get("original") or {}
    swap = entry.get("swapped") or {}
    if orig and all(orig.get(f) == current[f] for f in LANG_FILES):
        status = "original"
    elif swap and all(swap.get(f) == current[f] for f in LANG_FILES):
        status = "swapped"
    else:
        status = "unknown"

    info("")
    info(bold("当前状态："))
    if status == "original":
        ok("语言包：" + green("原始"))
    elif status == "swapped":
        ok("语言包：" + cyan("已互换（CHS<->JP）"))
    else:
        warn("语言包：" + yellow("未知（可能游戏已更新或被手动改过）"))

    if pref_path is None:
        warn(f"{USER_PREF_FILE}：" + yellow("此平台无法定位（Linux 非 Steam 版？）"))
    else:
        existing = read_user_pref(pref_path)
        if existing == USER_PREF_VALUE:
            ok(f"{USER_PREF_FILE}：" + green(f"已设置为 {USER_PREF_VALUE}"))
        elif existing is None:
            warn(f"{USER_PREF_FILE}：" + yellow("未设置"))
        else:
            warn(f"{USER_PREF_FILE}：" + yellow(f"当前值为 {existing!r}（非 {USER_PREF_VALUE}）"))
    return status, current


def pause_before_exit() -> None:
    try:
        input("\n按回车键退出...")
    except EOFError:
        pass


def detect_install(version: str) -> tuple[Path | None, str | None]:
    """根据版本检测安装目录。返回 (install_dir, steam_appid_if_steam)。"""
    if version == "steam":
        info("正在检测 Steam 安装...")
        roots = find_steam_root()
        if not roots:
            warn("未找到 Steam，可能未安装或路径非标。")
            return None, None
        for root in roots:
            ok(f"Steam: {root}")
            hit = find_steam_game(root)
            if hit:
                return hit[0], hit[1]
        warn(f"在 Steam 库里没找到「{GAME_DISPLAY_NAME}」。")
        return None, None
    if version == "xbox":
        info("正在检测 Xbox / Game Pass 安装...")
        hit = find_xbox_game()
        if hit:
            return hit, None
        warn(f"在 XboxGames 里没找到「{GAME_DISPLAY_NAME}」。")
        return None, None
    return None, None


def run(args: argparse.Namespace) -> int:
    banner()

    if IS_WINDOWS and not is_admin_windows() and not args.no_uac:
        info("尝试以管理员身份重启（用于写入受保护路径）...")
        if relaunch_as_admin_windows():
            # 已弹出提权进程，本进程立即退出，避免双窗口同时停在 "按回车键退出"
            sys.exit(0)
        warn("提权失败或被取消，继续以普通权限运行（Xbox 版可能写不进去）。")

    non_interactive = args.apply or args.revert or args.status

    if args.path:
        version = "manual"
        install_dir = Path(args.path).expanduser()
        steam_appid = None
    elif non_interactive:
        # 非交互模式无法弹版本选择菜单，按 platform 默认尝试 Steam
        version = "steam"
        install_dir, steam_appid = detect_install(version)
    else:
        version = prompt_version_choice()
        install_dir, steam_appid = detect_install(version)
        install_dir = confirm_install_dir(install_dir)
    if install_dir is None:
        fail("未确定游戏目录，退出（非交互模式请加 --path 指定）。" if non_interactive
             else "未确定游戏目录，退出。")
        return 1

    info("")
    info("正在校验游戏文件...")
    try:
        stringtables = validate_install(install_dir)
    except SwapError as e:
        fail(str(e))
        return 1
    ok(f"找到 {stringtables}")
    for f in LANG_FILES:
        size = (stringtables / f).stat().st_size
        ok(f"{f} ({size / (1024 * 1024):.1f} MiB)")

    if is_game_running():
        fail("游戏进程正在运行，请先退出游戏。")
        return 1
    ok("游戏未运行")

    state = load_state()
    install_key = _install_key(install_dir)
    pref_path = user_pref_path(version, steam_appid)

    status, current_hashes = describe_state(install_key, stringtables, pref_path, state)

    info("")
    if args.status:
        return 0

    if args.apply:
        if status == "swapped":
            info("已经处于互换状态，无需重复 apply。")
            return 0
        if status == "unknown" and not args.force:
            fail("当前状态未知，拒绝盲操作。确认无误后加 --force 再试。")
            return 2
        return do_apply(install_key, stringtables, current_hashes, pref_path,
                        state, fresh=(status == "unknown"), interactive=False)

    if args.revert:
        if status != "swapped":
            info(f"当前状态为 {status}，无需还原。")
            return 0
        return do_revert(install_key, stringtables, pref_path, state)

    if status == "swapped":
        info(bold("操作选项："))
        info("  [1] 还原为原始语言包")
        info("  [2] 仅重写 UserPreferredLang（无操作 zip）")
        info("  [Q] 退出")
        choice = ask("请选择", default="Q").lower()
        if choice == "1":
            return do_revert(install_key, stringtables, pref_path, state)
        if choice == "2":
            return do_pref_only(pref_path)
        return 0

    info(bold("操作选项："))
    info("  [1] 应用：中文 UI + 日语配音")
    info("  [Q] 退出")
    choice = ask("请选择", default="Q").lower()
    if choice == "1":
        return do_apply(install_key, stringtables, current_hashes, pref_path,
                        state, fresh=(status == "unknown"))
    return 0


def do_apply(install_key: str, stringtables: Path, current_hashes: dict,
             pref_path: Path | None, state: dict, fresh: bool,
             interactive: bool = True) -> int:
    if fresh:
        warn("当前哈希与已知状态都不匹配（可能游戏已更新或被手动改过）。")
        if interactive and not ask_yes_no(
                "是否把当前文件视为新的原始状态并互换？", default=False):
            info("已取消。")
            return 0
    info("执行互换...")
    try:
        swap_zips(stringtables)
    except Exception as e:
        fail(f"互换失败：{e}")
        return 1
    ok("CHS.zip <-> JP.zip 互换完成")

    swapped_hashes = {f: sha256_of(stringtables / f) for f in LANG_FILES}
    state[install_key] = {"original": current_hashes, "swapped": swapped_hashes}
    try:
        save_state(state)
        ok(f"状态已记录到 {state_file_path()}")
    except Exception as e:
        warn(f"写状态文件失败：{e}（不影响游戏，但下次启动可能识别不到状态）")

    if pref_path is None:
        warn(f"跳过 {USER_PREF_FILE}：当前平台无法定位 AppData 路径。")
    else:
        existing = read_user_pref(pref_path)
        if existing == USER_PREF_VALUE:
            ok(f"{USER_PREF_FILE} 已存在且为 {USER_PREF_VALUE}，无需重写")
        else:
            try:
                write_user_pref(pref_path)
                ok(f"已写入 {pref_path}")
            except Exception as e:
                fail(f"写 {USER_PREF_FILE} 失败：{e}")

    info("")
    info(green(bold("完成！启动游戏后即可享受中文 UI + 日语配音。")))
    info(yellow("提示：游戏更新可能会还原 zip 互换，那时再跑一次本工具即可。"))
    return 0


def do_revert(install_key: str, stringtables: Path, pref_path: Path | None,
              state: dict) -> int:
    info("执行还原...")
    try:
        swap_zips(stringtables)
    except Exception as e:
        fail(f"还原失败：{e}")
        return 1
    ok("CHS.zip <-> JP.zip 已交换回原状态")

    # 还原后，新的 hash 应该匹配 original
    new_hashes = {f: sha256_of(stringtables / f) for f in LANG_FILES}
    entry = state.get(install_key, {})
    entry["original"] = new_hashes
    entry.pop("swapped", None)
    state[install_key] = entry
    try:
        save_state(state)
    except Exception as e:
        warn(f"写状态文件失败：{e}")

    if pref_path is not None and pref_path.exists():
        if read_user_pref(pref_path) == USER_PREF_VALUE:
            try:
                pref_path.unlink()
                ok(f"已删除 {pref_path}")
            except Exception as e:
                warn(f"删除 {USER_PREF_FILE} 失败：{e}")
        else:
            warn(f"{USER_PREF_FILE} 内容已被改过，保留不动。")

    info(green(bold("已还原。")))
    return 0


def do_pref_only(pref_path: Path | None) -> int:
    if pref_path is None:
        fail("当前平台无法定位 AppData 路径。")
        return 1
    try:
        write_user_pref(pref_path)
        ok(f"已写入 {pref_path}")
        return 0
    except Exception as e:
        fail(f"写入失败：{e}")
        return 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--path", help="直接指定游戏安装目录（跳过自动检测）")
    p.add_argument("--no-uac", action="store_true", help="不在 Windows 上自动请求管理员")
    p.add_argument("--no-pause", action="store_true", help="结束时不暂停（CI / 脚本调用）")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true",
                   help="非交互：直接应用（中文 UI + 日语配音）")
    g.add_argument("--revert", action="store_true",
                   help="非交互：还原为原始语言包")
    g.add_argument("--status", action="store_true",
                   help="非交互：仅打印当前状态后退出")
    p.add_argument("--force", action="store_true",
                   help="--apply 时即使状态未知也强行覆盖记录并互换")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        rc = run(args)
    except KeyboardInterrupt:
        info("\n已中断。")
        rc = 130
    except Exception as e:
        fail(f"未处理的异常：{e}")
        import traceback
        traceback.print_exc()
        rc = 2
    non_interactive = args.apply or args.revert or args.status
    if not args.no_pause and not non_interactive:
        pause_before_exit()
    return rc


if __name__ == "__main__":
    sys.exit(main())
