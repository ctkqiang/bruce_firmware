#!/usr/bin/env python3
"""Package Nezha Cyber GitHub Release artifacts: manifest.json, SHA256SUMS.txt, and RELEASE_NOTES.md.

Includes per-firmware Flash size, App partition usage, RAM telemetry, edition tier
(full / standard / compact / lite), and 4MB compatibility indicators so users and
the Web Portal / CLI installer can pick the exact firmware suited to their hardware.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EDITION_LABELS = {
    "full": "Full (16MB)",
    "standard": "Standard (8MB)",
    "compact": "Compact (<4MB)",
    "lite": "Launcher Lite (<4MB)",
}

CHANGELOG_GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("feat", "### 🚀 New Features / 新功能", ("feat",)),
    ("fix", "### 🐛 Bug Fixes / 问题修复", ("fix",)),
    ("ui", "### 🎨 UI, Themes & Web Portal / 界面、主题与刷机台", ("ui", "style")),
    ("perf", "### ⚡ Performance & Memory / 性能与内存优化", ("perf",)),
    ("i18n", "### 🌍 i18n & CJK Fonts / 多语言与中文字库", ("i18n",)),
    ("other", "### 🔧 CI, Build, Security & Docs / 构建、CI、安全与文档", ("ci", "build", "chore", "docs", "refactor", "test", "sec", "security")),
]


def run_git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def find_previous_tag(current_tag: str, repo_dir: Path) -> str:
    raw = run_git(["tag", "-l", "v[0-9]*.[0-9]*", "--sort=-v:refname"], repo_dir)
    for line in raw.splitlines():
        tag = line.strip()
        if tag and tag != current_tag:
            return tag
    return ""


def build_changelog_markdown(current_tag: str, prev_tag: str, repo: str, repo_dir: Path) -> str:
    rev_range = f"{prev_tag}..HEAD" if prev_tag else "HEAD~30..HEAD"
    raw = run_git(["log", rev_range, "--no-merges", "--pretty=format:%h%x09%s%x09%an"], repo_dir)
    if not raw and not prev_tag:
        raw = run_git(["log", "-n", "30", "--no-merges", "--pretty=format:%h%x09%s%x09%an"], repo_dir)
    if not raw:
        return "_No new commit entries found for this release range._\n"

    buckets: dict[str, list[str]] = {k: [] for k, _, _ in CHANGELOG_GROUPS}
    seen_subjects: set[str] = set()
    conv_re = re.compile(r"^([a-zA-Z]+)(?:\(([^)]+)\))?(!)?:\s*(.+)$")

    for line in raw.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        short_sha, subject = parts[0].strip(), parts[1].strip()
        if not subject or subject in seen_subjects:
            continue
        seen_subjects.add(subject)

        m = conv_re.match(subject)
        bucket = "other"
        display = subject
        if m:
            ctype = m.group(1).lower()
            scope = (m.group(2) or "").strip()
            breaking = bool(m.group(3))
            desc = m.group(4).strip()
            if ctype in ("feat", "fix", "perf", "i18n"):
                bucket = ctype
            elif ctype in ("ui", "style") or (scope and any(k in scope.lower() for k in ("ui", "portal", "theme"))):
                bucket = "ui"
            else:
                bucket = "other"
            prefix = f"**{scope}:** " if scope else ""
            brk = "⚠️ **[BREAKING]** " if breaking else ""
            display = f"{brk}{prefix}{desc}"

        commit_url = f"https://github.com/{repo}/commit/{short_sha}"
        buckets[bucket].append(f"- {display} ([`{short_sha}`]({commit_url}))")

    sections: list[str] = []
    for key, heading, _ in CHANGELOG_GROUPS:
        items = buckets[key]
        if not items:
            continue
        capped = items[:25]
        body = "\n".join(capped)
        if len(items) > 25:
            body += f"\n- _...and {len(items) - 25} more commits._"
        sections.append(f"{heading}\n\n{body}")

    if prev_tag:
        compare_url = f"https://github.com/{repo}/compare/{prev_tag}...{current_tag}"
        sections.append(f"**Full Changelog / 完整提交对比:** [`{prev_tag}...{current_tag}`]({compare_url})")

    return "\n\n".join(sections) + "\n"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_partition_size(val: str) -> int:
    s = val.strip().upper()
    if s.endswith("K"):
        return int(s[:-1], 0) * 1024
    if s.endswith("M"):
        return int(s[:-1], 0) * 1024 * 1024
    return int(s, 0)


def read_app_partition_bytes(part_csv: Path) -> int:
    if not part_csv.is_file():
        return 0
    with part_csv.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().startswith("#") or len(row) < 5:
                continue
            subtype = row[2].strip().lower()
            if subtype in ("factory", "ota_0"):
                try:
                    return parse_partition_size(row[4])
                except ValueError:
                    return 0
    return 0


def parse_flash_mb(flash_str: str, fallback: int = 4) -> int:
    digits = "".join(ch for ch in str(flash_str) if ch.isdigit())
    return int(digits) if digits else fallback


def fmt_mb(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "—"
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    return f"{num_bytes / 1024:.0f} KB"


def infer_edition(env_name: str, flash_mb: int) -> str:
    if env_name.startswith("LAUNCHER_"):
        return "lite"
    if flash_mb <= 4:
        return "compact"
    if flash_mb <= 8:
        return "standard"
    return "full"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Nezha Cyber GitHub Release manifest and notes")
    parser.add_argument("--assets-dir", type=Path, default=Path("release-assets"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--prev-tag", default="", help="Previous release tag for changelog comparison")
    parser.add_argument("--repo-dir", type=Path, default=Path("."), help="Git repository directory for changelog")
    parser.add_argument("--channel", choices=["stable", "beta"], default=None)
    parser.add_argument("--repo", default="NezhaCyber/nezha_cyber_firmware")
    parser.add_argument("--notes-out", type=Path, default=Path("RELEASE_NOTES.md"))
    args = parser.parse_args()

    out_dir: Path = args.assets_dir
    if not out_dir.is_dir():
        print(f"ERROR: assets dir not found: {out_dir}", file=sys.stderr)
        return 1

    channel = args.channel or ("beta" if "-" in args.tag else "stable")
    release_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    metas: list[dict] = []
    for mf in sorted(out_dir.glob("meta-*.json")):
        data = json.loads(mf.read_text(encoding="utf-8"))
        env_name = data["env"]
        flash_mb = int(data.get("flash_mb") or parse_flash_mb(data.get("flash", "4MB")))
        flash_bytes = int(data.get("flash_bytes") or flash_mb * 1024 * 1024)
        edition = data.get("edition") or infer_edition(env_name, flash_mb)

        app_part_bytes = int(data.get("app_partition_bytes") or 0)
        if app_part_bytes <= 0 and data.get("partition"):
            app_part_bytes = read_app_partition_bytes(Path(data["partition"]))

        data["flash_mb"] = flash_mb
        data["flash_bytes"] = flash_bytes
        data["edition"] = edition
        data["edition_label"] = EDITION_LABELS.get(edition, edition)
        if app_part_bytes > 0:
            data["app_partition_bytes"] = app_part_bytes

        fbin = out_dir / data["factory_bin"]
        obin = out_dir / data["ota_bin"]
        factory_size = 0
        ota_size = 0
        if fbin.is_file():
            factory_size = fbin.stat().st_size
            data["factory_size"] = factory_size
            data["factory_sha256"] = sha256_file(fbin)
            data["factory_url"] = f"https://github.com/{args.repo}/releases/download/{args.tag}/{fbin.name}"
        if obin.is_file():
            ota_size = obin.stat().st_size
            data["ota_size"] = ota_size
            data["ota_sha256"] = sha256_file(obin)
            data["ota_url"] = f"https://github.com/{args.repo}/releases/download/{args.tag}/{obin.name}"

        effective_app = ota_size if ota_size > 0 else factory_size
        if app_part_bytes > 0 and effective_app > 0:
            data["flash_usage_pct"] = round((effective_app / app_part_bytes) * 100.0, 1)
            data["app_free_bytes"] = max(0, app_part_bytes - effective_app)
        elif flash_bytes > 0 and factory_size > 0:
            data["flash_usage_pct"] = round((factory_size / flash_bytes) * 100.0, 1)

        data["fits_flash"] = (factory_size <= flash_bytes) and (
            app_part_bytes <= 0 or effective_app <= app_part_bytes
        )
        data["fits_4mb"] = factory_size > 0 and factory_size <= 4 * 1024 * 1024

        metas.append(data)
        mf.unlink()

    manifest = {
        "project": "Nezha Cyber (哪吒网络安全)",
        "author": "钟智强 (ctkqiang)",
        "repository": args.repo,
        "version": args.tag,
        "channel": channel,
        "release_date": release_date,
        "schema_version": 2,
        "boards": metas,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    sum_lines = [
        f"{sha256_file(p)}  {p.name}"
        for p in sorted(out_dir.iterdir())
        if p.is_file() and p.suffix in (".bin", ".zip")
    ]
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(sum_lines) + "\n", encoding="utf-8")

    rows = []
    for m in metas:
        f_sz = fmt_mb(int(m.get("factory_size", 0)))
        o_sz = fmt_mb(int(m.get("ota_size", 0)))
        app_lim = fmt_mb(int(m.get("app_partition_bytes", 0)))
        usage_pct = m.get("flash_usage_pct")
        if usage_pct is not None and app_lim != "—":
            flash_cell = f"{o_sz if o_sz != '—' else f_sz} / {app_lim} (`{usage_pct}%`)"
        elif usage_pct is not None:
            flash_cell = f"{f_sz} / {m['flash']} (`{usage_pct}%`)"
        else:
            flash_cell = f"{f_sz} / {m['flash']}"

        ram_used = int(m.get("ram_used_bytes") or 0)
        ram_total = int(m.get("ram_total_bytes") or 0)
        if ram_used > 0 and ram_total > 0:
            ram_pct = round((ram_used / ram_total) * 100.0, 1)
            ram_cell = f"{ram_used // 1024} / {ram_total // 1024} KB (`{ram_pct}%`)"
        else:
            ram_cell = m.get("psram") or "320 KB SRAM"

        tier = m.get("edition_label", m.get("edition", ""))
        psram_note = f" + {m['psram']}" if m.get("psram") and m["psram"].lower() != "none" else ""
        rows.append(
            f"| **{m['board']}** | {tier} | `{m['mcu']}` · {m['flash']}{psram_note} "
            f"| `{m['factory_bin']}` ({f_sz}) | {flash_cell} | {ram_cell} |"
        )

    table_body = "\n".join(rows) if rows else "| *(no boards built)* | — | — | — | — | — |"
    channel_badge = "🧪 Beta / Pre-release" if channel == "beta" else "✅ Stable Release"
    owner = args.repo.split("/")[0].lower()
    repo_name = args.repo.split("/")[-1]
    portal_url = f"https://{owner}.github.io/{repo_name}/"
    prev_tag = args.prev_tag.strip() or find_previous_tag(args.tag, args.repo_dir.resolve())
    changelog_md = build_changelog_markdown(args.tag, prev_tag, args.repo, args.repo_dir.resolve())
    board_count = len(metas)
    commit_sha = run_git(["rev-parse", "--short", "HEAD"], args.repo_dir.resolve()) or "unknown"

    notes = (
        f"# 🔥 Nezha Cyber (哪吒网络安全) `{args.tag}` ({channel_badge})\n\n"
        f"**Release Date / 发布日期:** `{release_date}` · **Commit:** `{commit_sha}` · **Targets:** `{board_count}` boards  \n"
        f"**Author & Maintainer / 作者与维护者:** 钟智强 ([ctkqiang](https://github.com/ctkqiang))  \n"
        f"**Web Flasher Portal / 网页在线刷机台:** [{portal_url}]({portal_url})  \n"
        f"**Standalone Fork Lineage:** Independently evolved from Bruce Firmware "
        f"(full upstream Git history preserved under GNU AGPL-3.0).\n\n"
        f"---\n\n"
        f"## 📝 What's Changed / 本次更新内容\n\n"
        f"{changelog_md}\n"
        f"---\n\n"
        f"## 📦 Firmware Memory & Device Matrix / 各设备固件容量与内存占用\n\n"
        f"> **Hardware Memory Selection Guide / 设备选型提示**\n"
        f"> - **4 MB Devices (`M5StickC Plus 1.1`, `CYD-2432S028`)**: MUST use `< 4 MB` builds — "
        f"choose **Compact (`<4MB`)** for full features or **Launcher Lite (`<4MB`)** for extra free LittleFS space & M5Launcher.\n"
        f"> - **8 MB Devices (`M5Stack Cardputer`, `M5StickC Plus2`)**: Use **Standard (`8MB`)** builds.\n"
        f"> - **16 MB + PSRAM Flagships (`LilyGO T-Deck`, `T-Deck Plus`, `T-Embed`, `CoreS3`, `Core2`)**: "
        f"Use **Full (`16MB`)** builds with full CJK font & PSRAM sprites.\n\n"
        f"| Target Board / 目标板卡 | Edition / 版本档位 | MCU & Memory / 芯片与存储 | Factory Image (`0x00000`) | App Flash Usage / 闪存占用 | Static DRAM / 内存 |\n"
        f"| :--- | :--- | :--- | :--- | :--- | :--- |\n"
        f"{table_body}\n\n"
        f"- **`NezhaCyber-*-ota.bin`**: App-only OTA / M5Launcher images (matched per board in `manifest.json`).\n"
        f"- **`NezhaCyber-sd-files.zip`**: Optional microSD card resources (JS scripts, IR/RF samples, themes).\n"
        f"- **`manifest.json`** & **`SHA256SUMS.txt`**: Machine-readable memory metadata (used by Web Portal & `make install-release`) and SHA-256 checksums.\n\n"
        f"## ⚡ Quick Install & Verification / 快速安装与校验\n\n"
        f"```sh\n"
        f"# Option 1: Browser Web Serial (no install needed) -> {portal_url}\n\n"
        f"# Option 2: CLI auto-detect chip & flash size and install this exact release\n"
        f"make install RELEASE={args.tag}\n\n"
        f"# Option 3: Verify SHA-256 checksums & flash with esptool\n"
        f"sha256sum -c SHA256SUMS.txt --ignore-missing\n"
        f"esptool --chip esp32s3 --port /dev/ttyACM0 --baud 921600 write_flash 0x00000 NezhaCyber-lilygo-t-deck.bin\n"
        f"esptool --chip esp32   --port /dev/ttyUSB0 --baud 921600 write_flash 0x00000 NezhaCyber-m5stack-cplus1_1.bin\n"
        f"```\n\n"
        f"---\n"
    )
    args.notes_out.write_text(notes, encoding="utf-8")
    print(f"Packaged release {args.tag} ({channel}): {len(metas)} boards, {len(sum_lines)} checksummed files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
