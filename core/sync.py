"""
数据同步模块 — 基于用户私有 Git 仓库
=========================================
支持将 data/ 目录同步到任意私有 Git 仓库（GitHub / GitLab / Gitee 等）。

安全说明：
  - api_keys.json 含敏感密钥，在 push 前用 AES-256-GCM 加密为 api_keys.json.enc，
    原文件通过 .gitignore 排除，绝不进入仓库。
  - 加密 passphrase 仅保存在本地 sync_config.json，不进入 git。
  - 其余文件（novels.db / lancedb / local_models.json / exports）直接 commit。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from base64 import b64decode, b64encode
from pathlib import Path
from typing import Optional

# ── 可选依赖（在 UI 层捕获 ImportError，向用户展示安装提示）──────────────
try:
    import git
    from git import Repo, InvalidGitRepositoryError, GitCommandError
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

from core.config import DATA_DIR

# ── 路径常量 ───────────────────────────────────────────────────────────────
SYNC_CONFIG_FILE = DATA_DIR / "sync_config.json"
GITIGNORE_FILE = DATA_DIR / ".gitignore"
API_KEYS_FILE = DATA_DIR / "api_keys.json"
API_KEYS_ENC_FILE = DATA_DIR / "api_keys.json.enc"

# 默认同步时排除的文件（追加到 .gitignore）
_DEFAULT_GITIGNORE = """\
# Neupen sync — sensitive files kept local only
api_keys.json
sync_config.json
__pycache__/
*.pyc
.DS_Store
"""

# ── 同步配置 I/O ────────────────────────────────────────────────────────────

def load_sync_config() -> dict:
    """读取本地同步配置（不进 git）"""
    if SYNC_CONFIG_FILE.exists():
        try:
            return json.loads(SYNC_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_sync_config(cfg: dict):
    """保存同步配置到本地（不进 git）"""
    SYNC_CONFIG_FILE.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── 加密 / 解密 ─────────────────────────────────────────────────────────────

def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """从 passphrase 派生 32 字节 AES 密钥（PBKDF2-HMAC-SHA256, 200k 轮）"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_api_keys(passphrase: str) -> bool:
    """
    将 api_keys.json 加密为 api_keys.json.enc。
    格式：[salt 16B][nonce 12B][ciphertext]，base64 存储。
    返回 True 表示加密成功，False 表示无需加密（文件不存在）。
    """
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("请先安装 cryptography：pip install cryptography")
    if not API_KEYS_FILE.exists():
        return False

    plaintext = API_KEYS_FILE.read_bytes()
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    payload = b64encode(salt + nonce + ciphertext).decode("ascii")
    API_KEYS_ENC_FILE.write_text(payload, encoding="ascii")
    return True


def decrypt_api_keys(passphrase: str) -> bool:
    """
    将 api_keys.json.enc 解密还原为 api_keys.json。
    返回 True 表示解密成功。
    抛出 ValueError 表示密码错误或文件损坏。
    """
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("请先安装 cryptography：pip install cryptography")
    if not API_KEYS_ENC_FILE.exists():
        return False

    raw = b64decode(API_KEYS_ENC_FILE.read_text(encoding="ascii"))
    salt, nonce, ciphertext = raw[:16], raw[16:28], raw[28:]
    key = _derive_key(passphrase, salt)
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception:
        raise ValueError("密码错误或加密文件已损坏，请检查同步密码")

    API_KEYS_FILE.write_bytes(plaintext)
    return True


# ── .gitignore 维护 ─────────────────────────────────────────────────────────

def _ensure_gitignore():
    """确保 data/.gitignore 包含必要的排除规则"""
    existing = GITIGNORE_FILE.read_text(encoding="utf-8") if GITIGNORE_FILE.exists() else ""
    if "api_keys.json" not in existing:
        with open(GITIGNORE_FILE, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(_DEFAULT_GITIGNORE)


# ── Git 仓库初始化 ───────────────────────────────────────────────────────────

def init_or_open_repo() -> "Repo":
    """
    打开或初始化 data/ 目录的 git 仓库。
    如果 data/ 下还没有 .git，则 `git init`。
    """
    if not GIT_AVAILABLE:
        raise RuntimeError("请先安装 gitpython：pip install gitpython")
    try:
        repo = Repo(DATA_DIR)
    except InvalidGitRepositoryError:
        repo = Repo.init(DATA_DIR)
        # 配置 git 用户（仅限本仓库）
        repo.config_writer().set_value("user", "name", "Neupen Sync").release()
        repo.config_writer().set_value("user", "email", "neupen@local").release()
    return repo


def set_remote(repo: "Repo", remote_url: str, branch: str = "main"):
    """设置或更新远端 origin"""
    try:
        origin = repo.remote("origin")
        if origin.url != remote_url:
            origin.set_url(remote_url)
    except ValueError:
        repo.create_remote("origin", remote_url)


# ── 状态查询 ────────────────────────────────────────────────────────────────

def get_sync_status(repo: "Repo") -> dict:
    """
    返回当前同步状态字典：
    {
        "local_commits":  int,   # 本地领先远端的 commit 数
        "remote_commits": int,   # 远端领先本地的 commit 数（需要先 fetch）
        "last_commit_hash": str,
        "last_commit_msg":  str,
        "last_commit_time": str,
        "is_dirty": bool,        # 有未 commit 的改动
        "conflict": bool,        # 双向都有新 commit（需要手动解决）
    }
    """
    result: dict = {
        "local_commits": 0,
        "remote_commits": 0,
        "last_commit_hash": "",
        "last_commit_msg": "",
        "last_commit_time": "",
        "is_dirty": False,
        "conflict": False,
    }
    try:
        result["is_dirty"] = repo.is_dirty(untracked_files=True)
        if repo.heads:
            head = repo.head.commit
            result["last_commit_hash"] = head.hexsha[:8]
            result["last_commit_msg"] = head.message.strip()
            result["last_commit_time"] = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(head.committed_date)
            )
        # 比较本地与远端（需要已 fetch）
        cfg = load_sync_config()
        branch = cfg.get("branch", "main")
        if repo.remotes and branch in repo.heads:
            try:
                local_ref = repo.heads[branch]
                remote_ref = repo.remotes["origin"].refs[branch]
                ahead = list(repo.iter_commits(f"{remote_ref}..{local_ref}"))
                behind = list(repo.iter_commits(f"{local_ref}..{remote_ref}"))
                result["local_commits"] = len(ahead)
                result["remote_commits"] = len(behind)
                result["conflict"] = len(ahead) > 0 and len(behind) > 0
            except Exception:
                pass
    except Exception:
        pass
    return result


# ── Push ────────────────────────────────────────────────────────────────────

def push(
    passphrase: Optional[str] = None,
    commit_message: str = "",
) -> str:
    """
    将 data/ 目录推送到远端 git 仓库。

    流程：
    1. 加密 api_keys.json → api_keys.json.enc
    2. 确保 .gitignore 正确
    3. git add -A（排除 gitignore 的文件）
    4. git commit（若有改动）
    5. git push

    返回操作摘要字符串（成功或错误信息）。
    """
    cfg = load_sync_config()
    remote_url = cfg.get("remote_url", "").strip()
    branch = cfg.get("branch", "main").strip()
    if not remote_url:
        raise ValueError("尚未配置远端仓库地址，请在云同步设置中填写")

    repo = init_or_open_repo()
    set_remote(repo, remote_url, branch)
    _ensure_gitignore()

    # 加密 api_keys
    if passphrase:
        encrypt_api_keys(passphrase)

    # Stage all
    repo.git.add("-A")

    # Commit（若有改动）
    if repo.is_dirty(index=True, untracked_files=False) or repo.untracked_files:
        msg = commit_message or f"sync: Neupen data backup {time.strftime('%Y-%m-%d %H:%M')}"
        repo.index.commit(msg)

    # Push
    try:
        origin = repo.remote("origin")
        push_result = origin.push(refspec=f"HEAD:refs/heads/{branch}", set_upstream=True)
        for info in push_result:
            if info.flags & info.ERROR:
                raise GitCommandError("push", info.summary)
    except GitCommandError as e:
        raise RuntimeError(f"推送失败：{e}") from e

    return f"已推送到 {remote_url}（分支 {branch}）"


# ── Pull ────────────────────────────────────────────────────────────────────

def pull(
    passphrase: Optional[str] = None,
    strategy: str = "ask",
) -> str:
    """
    从远端 git 仓库拉取数据。

    strategy:
      "remote" — 远端覆盖本地（fetch + reset --hard）
      "local"  — 本地覆盖远端（忽略远端变更，仅用于无冲突场景）
      "ask"    — 检测到冲突时抛出 ConflictError，由 UI 层处理

    拉取成功后自动解密 api_keys.json.enc → api_keys.json（如果有 passphrase）。
    返回操作摘要字符串。
    """
    cfg = load_sync_config()
    remote_url = cfg.get("remote_url", "").strip()
    branch = cfg.get("branch", "main").strip()
    if not remote_url:
        raise ValueError("尚未配置远端仓库地址，请在云同步设置中填写")

    repo = init_or_open_repo()
    set_remote(repo, remote_url, branch)
    _ensure_gitignore()

    origin = repo.remote("origin")

    # Fetch
    try:
        origin.fetch()
    except GitCommandError as e:
        raise RuntimeError(f"拉取失败（fetch）：{e}") from e

    # 检查是否存在远端分支
    remote_ref_name = f"origin/{branch}"
    if remote_ref_name not in [r.name for r in repo.remotes["origin"].refs]:
        return "远端分支不存在，无需拉取"

    remote_ref = repo.remotes["origin"].refs[branch]

    # 检测冲突
    if repo.heads:
        local_ref = repo.heads[branch] if branch in [h.name for h in repo.heads] else None
        if local_ref:
            ahead = list(repo.iter_commits(f"{remote_ref}..{local_ref}"))
            behind = list(repo.iter_commits(f"{local_ref}..{remote_ref}"))
            has_conflict = len(ahead) > 0 and len(behind) > 0

            if has_conflict:
                if strategy == "ask":
                    raise ConflictError(
                        local_ahead=len(ahead),
                        remote_ahead=len(behind),
                    )
                elif strategy == "local":
                    # 以本地为准：push force（不改动本地文件，只是跳过 pull）
                    return "已选择保留本地数据，远端将在下次推送时被覆盖"
                # strategy == "remote": 继续执行 reset

    # Reset hard to remote
    try:
        if branch not in [h.name for h in repo.heads]:
            # 本地没有该分支，创建并跟踪
            repo.git.checkout("-b", branch, f"origin/{branch}")
        else:
            repo.git.reset("--hard", f"origin/{branch}")
    except GitCommandError as e:
        raise RuntimeError(f"拉取失败（reset）：{e}") from e

    # 解密 api_keys
    if passphrase and API_KEYS_ENC_FILE.exists():
        decrypt_api_keys(passphrase)

    return f"已从 {remote_url}（分支 {branch}）拉取最新数据"


# ── Fetch（仅刷新远端状态）──────────────────────────────────────────────────

def fetch_remote() -> bool:
    """仅执行 fetch，刷新远端引用，用于在 UI 中显示最新状态。返回是否成功。"""
    cfg = load_sync_config()
    remote_url = cfg.get("remote_url", "").strip()
    if not remote_url:
        return False
    try:
        repo = init_or_open_repo()
        set_remote(repo, remote_url)
        repo.remote("origin").fetch()
        return True
    except Exception:
        return False


# ── 自定义异常 ──────────────────────────────────────────────────────────────

class ConflictError(Exception):
    """本地和远端都有新提交，需要用户手动决策"""
    def __init__(self, local_ahead: int, remote_ahead: int):
        self.local_ahead = local_ahead
        self.remote_ahead = remote_ahead
        super().__init__(
            f"冲突：本地领先 {local_ahead} 个提交，远端领先 {remote_ahead} 个提交"
        )
