# Strata Universal Model Importer / Strata 通用模型导入与适配工具
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def sanitize_name(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]", "_", name.strip().lower())
    return re.sub(r"_+", "_", s).strip("_")


def find_strata_python(strata_dir: Path) -> Path:
    # 1. venv in strata_dir
    venv_py = strata_dir / ".venv" / "Scripts" / "python.exe"
    if venv_py.is_file():
        return venv_py
    venv_py_linux = strata_dir / ".venv" / "bin" / "python"
    if venv_py_linux.is_file():
        return venv_py_linux
    # 2. current running python
    return Path(sys.executable)


def find_strata_data_dir(strata_dir: Path) -> Path:
    # 1. Check settings.json
    appdata = os.environ.get("APPDATA")
    if appdata:
        cfg = Path(appdata) / "Strata" / "settings.json"
        if cfg.is_file():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                if data.get("data_dir") and Path(data["data_dir"]).is_dir():
                    return Path(data["data_dir"]).resolve()
            except Exception:
                pass
    # 2. Sibling Strata-data
    cand = strata_dir.parent / "Strata-data"
    if cand.is_dir():
        return cand.resolve()
    cand2 = strata_dir / "Strata-data"
    if cand2.is_dir():
        return cand2.resolve()
    # 3. Fallback to strata_dir/data
    return (strata_dir / "data").resolve()


def inspect_model_path(model_path: Path, strata_dir: Path):
    sys.path.insert(0, str(strata_dir / "tools"))
    try:
        import gguf_reader as G
    except Exception as e:
        print(f"[Error] Failed to load gguf_reader from {strata_dir / 'tools'}: {e}")
        sys.exit(1)

    if model_path.is_file():
        candidates = [model_path]
        parent = model_path.parent
    else:
        candidates = list(model_path.glob("*.gguf"))
        parent = model_path

    valid_models = []
    for f in candidates:
        name_lower = f.name.lower()
        if name_lower.startswith("mmproj") or "refusal-direction" in name_lower:
            continue
        try:
            gf = G.GGUFFile(f)
            arch = gf.metadata.get("general.architecture", "")
            if arch == "controlvector":
                continue
            valid_models.append((f, gf))
        except Exception:
            continue

    if not valid_models:
        print(f"[Error] No valid model GGUF found in: {model_path}")
        sys.exit(1)

    shard1 = None
    shard2 = None
    for f, gf in valid_models:
        if "-00001-of-" in f.name:
            shard1 = (f, gf)
            m = re.search(r"-(00001)-of-(\d{5})\.gguf$", f.name)
            if m:
                total = int(m.group(2))
                s2_name = f.name[: m.start()] + f"-00002-of-{total:05d}.gguf"
                s2_path = parent / s2_name
                if s2_path.exists():
                    try:
                        shard2 = (s2_path, G.GGUFFile(s2_path))
                    except Exception:
                        pass
            break

    if not shard1:
        shard1 = valid_models[0]

    return shard1, shard2, parent


# Long vendor / series prefixes are dropped from auto-generated tags so the tag stays
# readable. Edit this list to match your own naming scheme (an empty tuple keeps all).
#
# The default entry covers SC117's abliterated GSQ-RCO builds:
# https://huggingface.co/SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF
_TAG_VENDOR_PREFIXES: tuple[str, ...] = (
    r"Qwen3\.8-Flash-Next-GSQ-RCO-",
)


def import_model(
    strata_dir: Path,
    model_path: Path,
    custom_tag: str | None = None,
    context_length: int = 131072,
    port: int = 8080,
    force_repack: bool = False,
    data_dir_override: Path | None = None,
    mtp_file_override: Path | None = None,
):
    strata_dir = strata_dir.resolve()
    model_path = model_path.resolve()

    if not strata_dir.is_dir():
        print(f"[Error] Strata project directory not found: {strata_dir}")
        return False

    pack_tool = strata_dir / "tools" / "iq_pack.py"
    if not pack_tool.is_file():
        print(f"[Error] Invalid Strata directory (missing tools/iq_pack.py): {strata_dir}")
        return False

    if not model_path.exists():
        print(f"[Error] Model path not found: {model_path}")
        return False

    data_dir = data_dir_override.resolve() if data_dir_override else find_strata_data_dir(strata_dir)
    python_exe = find_strata_python(strata_dir)

    print("\n" + "=" * 60)
    print("      Strata Universal Model Importer / 通用导入工具")
    print("=" * 60)
    print(f"  Strata Project : {strata_dir}")
    print(f"  Data Directory : {data_dir}")
    print(f"  Python Runtime : {python_exe}")
    print(f"  Model Path     : {model_path}")

    shard1, shard2, model_folder = inspect_model_path(model_path, strata_dir)
    s1_path, s1_gf = shard1
    print(f"  Main Shard     : {s1_path.name}")
    if shard2:
        print(f"  PLE Shard      : {shard2[0].name}")

    if custom_tag:
        tag = sanitize_name(custom_tag)
    else:
        stem = s1_path.stem
        stem = re.sub(r"-00001-of-\d{5}", "", stem)
        for _prefix in _TAG_VENDOR_PREFIXES:
            stem = re.sub(_prefix, "", stem, flags=re.I)
        tag = sanitize_name(stem)
        if not tag:
            tag = "custom_model"

    pack_dir = data_dir / "packs" / tag
    print(f"  Model Tag      : {tag}")
    print(f"  Target Pack    : {pack_dir}")

    # Step 1: Pack generation
    need_pack = (
        force_repack
        or not (pack_dir / "native_experts.txt").exists()
        or not (pack_dir / "tokenizer" / "vocab.json").exists()
    )
    if need_pack:
        print(f"\n[Step 1/3] Generating native pack for {tag}...")
        pack_dir.mkdir(parents=True, exist_ok=True)
        cmd = [str(python_exe), str(pack_tool), "--gguf", str(s1_path), "--out", str(pack_dir)]
        ret = subprocess.run(cmd, cwd=str(strata_dir))
        if ret.returncode != 0:
            print(f"[Error] Failed to generate pack for {s1_path}")
            return False
        print("  [ok] Pack generated successfully.")
    else:
        print(f"\n[Step 1/3] Existing pack found at {pack_dir}, skipping repack.")

    # Step 1.5: MTP Draft Layer Detection & Auto-build
    mtp_rt_dir = data_dir / "mtp" / "rt"
    has_valid_mtp = (mtp_rt_dir / "experts.bin").is_file() and (mtp_rt_dir / "dense.bin").is_file()

    if not has_valid_mtp:
        # Check candidate mtp-q2_0.gguf
        cand_mtp_gguf = []
        if mtp_file_override and mtp_file_override.is_file():
            cand_mtp_gguf.append(mtp_file_override)
        cand_mtp_gguf.extend([
            model_folder / "mtp-q2_0.gguf",
            data_dir / "mtp" / "mtp-q2_0.gguf",
            model_folder / "strata" / "mtp-q2_0.gguf"
        ])
        found_mtp_gguf = next((g for g in cand_mtp_gguf if g.is_file()), None)
        mtp_tool = strata_dir / "tools" / "mtp_rt.py"

        if found_mtp_gguf and mtp_tool.is_file():
            print(f"\n[MTP Setup] Found MTP GGUF at {found_mtp_gguf.name}, compiling runtime draft layer...")
            mtp_rt_dir.mkdir(parents=True, exist_ok=True)
            cmd_mtp = [str(python_exe), str(mtp_tool), "--gguf", str(found_mtp_gguf), "--out", str(mtp_rt_dir)]
            ret_mtp = subprocess.run(cmd_mtp, cwd=str(strata_dir))
            if ret_mtp.returncode == 0 and (mtp_rt_dir / "experts.bin").is_file():
                has_valid_mtp = True
                print(f"  [ok] MTP runtime successfully built at {mtp_rt_dir}")
            else:
                print("  [Warning] Failed to compile MTP runtime, will run without speculative decoding.")
        else:
            print("\n[MTP Setup] No pre-compiled MTP runtime or mtp-q2_0.gguf found. Model will launch safely without MTP.")

    # Step 2: Generate Strata JSON config
    print(f"\n[Step 2/3] Generating Strata launch configuration...")
    base_template = None
    # Search for an existing strata-*.json in strata_dir to clone hardware settings
    for cand_json in list(strata_dir.glob("strata-*.json")):
        if cand_json.name.endswith(".bak") or "component" in cand_json.name:
            continue
        try:
            base_template = json.loads(cand_json.read_text(encoding="utf-8"))
            if "exe" in base_template and "args" in base_template:
                print(f"  Inherited hardware presets from: {cand_json.name}")
                break
        except Exception:
            continue

    if not base_template:
        # Minimal fallback config if no json found in strata_dir
        engine_exe = strata_dir / "engine" / ("strata.exe" if os.name == "nt" else "strata")
        base_template = {
            "exe": str(engine_exe),
            "args": [
                "--pack", str(pack_dir),
                "--native", str(s1_path),
                "--expert-profile", str(strata_dir / "data" / "expert-profile.bin"),
                "--expert-cache", "auto",
                "--prefill", "auto",
                "--spec", "4",
                "--mtp", str(data_dir / "mtp" / "rt"),
                "--max-context", str(context_length),
                "--kv", "int8",
                "--kv-resident", "32768",
                "--vision",
                "--vram-reserve-mib", "700"
            ],
            "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 20},
            "cwd": str(strata_dir),
            "port": port
        }
        if shard2:
            base_template["args"].extend(["--ple-gguf", str(shard2[0])])

    cfg = dict(base_template)
    cfg["model_name"] = f"strata-{tag}"
    cfg["tokenizer"] = str(pack_dir / "tokenizer")
    cfg["log"] = str(strata_dir / f"strata-{tag}.log")
    cfg["port"] = port
    cfg["cwd"] = str(strata_dir)

    def patch_args(arg_list):
        new_list = []
        i = 0
        has_ple = False
        while i < len(arg_list):
            item = arg_list[i]
            if item == "--pack":
                new_list.extend(["--pack", str(pack_dir)])
                i += 2
            elif item == "--native":
                new_list.extend(["--native", str(s1_path)])
                i += 2
            elif item == "--ple-gguf":
                has_ple = True
                if shard2:
                    new_list.extend(["--ple-gguf", str(shard2[0])])
                i += 2
            elif item == "--mtp":
                if has_valid_mtp:
                    new_list.extend(["--mtp", str(mtp_rt_dir)])
                i += 2
            elif item in ("--spec", "--spec-min-p"):
                if has_valid_mtp:
                    new_list.extend([item, arg_list[i + 1]])
                i += 2
            elif item == "--max-context":
                new_list.extend(["--max-context", str(context_length)])
                i += 2
            else:
                new_list.append(item)
                i += 1
        if not has_ple and shard2:
            new_list.extend(["--ple-gguf", str(shard2[0])])
        return new_list

    cfg["args"] = patch_args(cfg.get("args", []))
    if "args_written" in cfg:
        cfg["args_written"] = patch_args(cfg.get("args_written", []))

    if "vision" in cfg and isinstance(cfg["vision"], dict):
        cfg["vision"]["model"] = str(s1_path)

    config_path = strata_dir / f"strata-{tag}.json"
    config_path.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    print(f"  [ok] Config created: {config_path}")

    # Step 3: Generate Launcher Batch / Shell Script
    print(f"\n[Step 3/3] Generating launcher scripts...")
    server_py = strata_dir / "serve" / "server.py"

    if os.name == "nt":
        bat_path = strata_dir / f"run-{tag}.bat"
        bat_lines = [
            "@echo off",
            f"title Strata Model: {tag}",
            f'cd /d "{strata_dir}"',
            f'"{python_exe}" "{server_py}" --engine strata --config "{config_path}" --port {port} --open',
            "if errorlevel 1 pause",
        ]
        with open(bat_path, "wb") as f:
            f.write("\r\n".join(bat_lines).encode("ascii") + b"\r\n")
        print(f"  [ok] Launcher created: {bat_path}")

        # Also create a desktop-friendly launcher inside importer folder
        local_bat = Path(__file__).resolve().parent / f"run-{tag}.bat"
        with open(local_bat, "wb") as f:
            f.write("\r\n".join(bat_lines).encode("ascii") + b"\r\n")
        print(f"  [ok] Shortcut created in importer folder: {local_bat.name}")
    else:
        sh_path = strata_dir / f"run-{tag}.sh"
        sh_lines = [
            "#!/bin/sh",
            f'cd "{strata_dir}"',
            f'"{python_exe}" "{server_py}" --engine strata --config "{config_path}" --port {port} --open',
        ]
        sh_path.write_text("\n".join(sh_lines) + "\n", encoding="utf-8")
        sh_path.chmod(0o755)
        print(f"  [ok] Launcher created: {sh_path}")

    print("\n" + "=" * 60)
    print("      Model Import Successfully Completed! / 导入成功！")
    print("=" * 60)
    print(f"Model Identifier : {tag}")
    print(f"Config File      : {config_path.name}")
    if os.name == "nt":
        print(f"Launch via       : run-{tag}.bat")
    print("=" * 60)
    return True


def prompt_folder(title: str, default: Path | None = None) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title=title, initialdir=str(default) if default else None)
        root.destroy()
        if path:
            return Path(path)
    except Exception:
        pass
    return None


def prompt_file(title: str, filetypes: list[tuple[str, str]], default: Path | None = None) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(title=title, filetypes=filetypes, initialdir=str(default) if default else None)
        root.destroy()
        if path:
            return Path(path)
    except Exception:
        pass
    return None


def interactive_mode():
    print("=" * 65)
    print("    Strata Universal Model Importer (Interactive Mode)")
    print("    Strata 通用模型导入与启动器生成工具（交互与图形选择模式）")
    print("=" * 65)

    # 1. Select Strata directory
    default_strata = Path(__file__).resolve().parent.parent / "Strata"
    strata_dir = None
    if default_strata.is_dir() and (default_strata / "tools" / "iq_pack.py").is_file():
        strata_dir = default_strata

    print("\n[1/3] 请选择 Strata 项目根目录 (Strata project directory):")
    choice = input("按回车打开文件浏览器选择，或直接输入路径: ").strip().strip('"').strip("'")
    if choice:
        strata_dir = Path(choice)
    elif not strata_dir:
        strata_dir = prompt_folder("请选择 Strata 源码或安装主目录 (包含 engine/tools)")

    while not strata_dir or not (strata_dir / "tools" / "iq_pack.py").is_file():
        print(f"[Error] 路径无效或缺少 tools/iq_pack.py: {strata_dir}")
        sel = prompt_folder("请选择正确的 Strata 主目录 (包含 engine/tools)")
        if not sel:
            typed = input("请输入 Strata 路径 (输入 q 退出): ").strip().strip('"').strip("'")
            if typed.lower() == "q":
                sys.exit(0)
            sel = Path(typed)
        strata_dir = sel

    print(f"-> 已选定 Strata 目录: {strata_dir}")

    # 2. Select Model Path
    print("\n[2/3] 请选择新模型路径 (GGUF 文件或所在文件夹):")
    model_choice = input("直接输入模型路径，或按回车打开文件浏览器选择: ").strip().strip('"').strip("'")
    model_path = None
    if model_choice:
        model_path = Path(model_choice)
    else:
        # Prompt user whether to select folder or file
        model_path = prompt_folder("选择模型所在文件夹 (如存放多个分卷或 GGUF 的目录)")
        if not model_path:
            model_path = prompt_file("选择主模型 GGUF 文件 (00001-of-00002.gguf 或单文件)", [("GGUF files", "*.gguf")])

    while not model_path or not model_path.exists():
        print(f"[Error] 模型路径不存在: {model_path}")
        sel = prompt_folder("选择模型所在文件夹")
        if not sel:
            sel = prompt_file("选择主模型 GGUF 文件", [("GGUF files", "*.gguf")])
        if not sel:
            typed = input("请输入模型路径 (输入 q 退出): ").strip().strip('"').strip("'")
            if typed.lower() == "q":
                sys.exit(0)
            sel = Path(typed)
        model_path = sel

    print(f"-> 已选定模型路径: {model_path}")

    # 3. Model Tag / Name
    print("\n[3/3] 设置模型标识代号 (留空将自动根据文件名生成):")
    tag_input = input("模型名称/标识 (如 my_abliterated_model): ").strip()
    custom_tag = tag_input if tag_input else None

    # Run importer
    import_model(strata_dir=strata_dir, model_path=model_path, custom_tag=custom_tag)


def main():
    parser = argparse.ArgumentParser(description="Universal Model Importer for Strata")
    parser.add_argument("--strata-dir", help="Path to the Strata project directory (the folder containing tools/iq_pack.py)")
    parser.add_argument("--model-path", help="Path to model GGUF file or directory")
    parser.add_argument("--name", help="Custom model identifier (e.g. my_abliterated)")
    parser.add_argument("--context", type=int, default=131072, help="Context length (default: 131072)")
    parser.add_argument("--port", type=int, default=8080, help="Web/API port (default: 8080)")
    parser.add_argument("--repack", action="store_true", help="Force rebuild pack even if exists")
    parser.add_argument("--data-dir", help="Optional override for Strata-data directory")
    parser.add_argument("--mtp-file", help="Path to mtp-q2_0.gguf file")
    args = parser.parse_args()

    # If launched with no arguments, enter interactive mode
    if len(sys.argv) == 1:
        interactive_mode()
        input("\n按回车键退出 (Press Enter to exit)...")
        return

    if not args.strata_dir or not args.model_path:
        # Fallback to interactive
        interactive_mode()
        return

    strata_dir = Path(args.strata_dir)
    model_path = Path(args.model_path)
    data_dir = Path(args.data_dir) if args.data_dir else None
    mtp_file = Path(args.mtp_file) if args.mtp_file else None

    success = import_model(
        strata_dir=strata_dir,
        model_path=model_path,
        custom_tag=args.name,
        context_length=args.context,
        port=args.port,
        force_repack=args.repack,
        data_dir_override=data_dir,
        mtp_file_override=mtp_file,
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
