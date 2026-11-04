# gui.py - Strata Universal Model Importer GUI
import os
import re
import sys
import json
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Enable High-DPI sharpness on Windows 10 & Windows 11
SCALE_FACTOR = 1.0
try:
    import ctypes
    # Try Per-Monitor DPI v2 / v1 (Windows 10 1607+)
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-2) # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1) # PROCESS_SYSTEM_DPI_AWARE (Win 8.1 / Win 10)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()

    hdc = ctypes.windll.user32.GetDC(0)
    if hdc:
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        SCALE_FACTOR = max(1.0, dpi / 96.0)
except Exception:
    SCALE_FACTOR = 1.0

ROOT = Path(__file__).resolve().parent

# Color Palette (Modern Dark Slate)
BG_COLOR = "#0f172a"        # Deep slate
CARD_BG = "#1e293b"         # Dark slate card
TEXT_MAIN = "#f8fafc"       # White text
TEXT_MUTED = "#94a3b8"      # Light slate text
ACCENT = "#3b82f6"          # Blue accent
ACCENT_HOVER = "#2563eb"
SUCCESS_COLOR = "#10b981"   # Emerald green
ENTRY_BG = "#334155"        # Slate entry background
BORDER_COLOR = "#475569"


def auto_detect_strata() -> str:
    # Look for generic Strata directory beside importer without hardcoded user names
    parent = ROOT.parent
    cands = [parent / "Strata", parent]
    for c in cands:
        if c.is_dir() and (c / "tools" / "iq_pack.py").is_file():
            return str(c.resolve())
    return ""


def auto_detect_model_dir() -> str:
    return ""


class StrataImporterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Strata Universal Model Importer")

        win_w = int(780 * SCALE_FACTOR)
        win_h = int(820 * SCALE_FACTOR)
        min_w = int(680 * SCALE_FACTOR)
        min_h = int(720 * SCALE_FACTOR)

        self.root.geometry(f"{win_w}x{win_h}")
        self.root.minsize(min_w, min_h)
        self.root.configure(bg=BG_COLOR)

        # Variables - Blank defaults to ensure clean sharing without personal paths
        self.strata_dir_var = tk.StringVar(value="")
        self.model_path_var = tk.StringVar(value="")
        self.mtp_file_var = tk.StringVar(value="")
        self.model_name_var = tk.StringVar()
        self.context_var = tk.StringVar(value="131072 (128K 推荐)")
        self.port_var = tk.StringVar(value="8080")
        self.status_var = tk.StringVar(value="准备就绪，请选择 Strata 目录与模型路径后开始导入")
        self.is_running = False

        self._build_ui()

    def _build_ui(self):
        # Header
        header_frame = tk.Frame(self.root, bg=BG_COLOR, pady=15)
        header_frame.pack(fill="x", padx=25)

        title_lbl = tk.Label(
            header_frame,
            text="Strata 模型一键导入与适配器",
            font=("Microsoft YaHei UI", 18, "bold"),
            bg=BG_COLOR,
            fg=TEXT_MAIN
        )
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            header_frame,
            text="免命令下一键适配消融版 (Abliterated)、去限制版及第三方社区 GGUF 模型",
            font=("Microsoft YaHei UI", 10),
            bg=BG_COLOR,
            fg=TEXT_MUTED
        )
        sub_lbl.pack(anchor="w", pady=(4, 0))

        # Main Card Container
        card = tk.Frame(self.root, bg=CARD_BG, padx=20, pady=18, highlightbackground=BORDER_COLOR, highlightthickness=1)
        card.pack(fill="x", padx=25, pady=5)

        # 1. Strata Folder Row (Grid rows: 0, 1, 2)
        self._create_input_row(
            card,
            row=0,
            label="Strata 项目主目录 (Strata Directory):",
            tip="包含 engine/ 和 tools/ 文件夹的 Strata 目录",
            var=self.strata_dir_var,
            btn_text="浏览文件夹...",
            btn_cmd=self._browse_strata_dir
        )

        # 2. Model Path Row (Grid rows: 3, 4, 5)
        self._create_input_row(
            card,
            row=3,
            label="新模型路径 (Model GGUF or Folder):",
            tip="可选择模型文件夹，也可直接选择第 1 分卷 .gguf 文件",
            var=self.model_path_var,
            btn_text="选择文件夹...",
            btn_cmd=self._browse_model_dir,
            extra_btn_text="选择单个文件...",
            extra_btn_cmd=self._browse_model_file
        )

        # 3. MTP File Selection (Grid rows: 6, 7, 8)
        self._create_input_row(
            card,
            row=6,
            label="MTP 草稿层 GGUF 文件 (可选):",
            tip="本地没有 mtp/rt 运行时，可在此提供 mtp-q2_0.gguf，工具将自动为你编译",
            var=self.mtp_file_var,
            btn_text="选择 MTP 文件...",
            btn_cmd=self._browse_mtp_file
        )

        # 4. Model Custom Name Row (Grid row: 9)
        name_frame = tk.Frame(card, bg=CARD_BG)
        name_frame.grid(row=9, column=0, columnspan=3, sticky="we", pady=(10, 5))
        name_frame.columnconfigure(1, weight=1)

        tk.Label(
            name_frame,
            text="自定义模型名称 (可选):",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=CARD_BG,
            fg=TEXT_MAIN
        ).grid(row=0, column=0, sticky="w")

        name_entry = tk.Entry(
            name_frame,
            textvariable=self.model_name_var,
            font=("Microsoft YaHei UI", 10),
            bg=ENTRY_BG,
            fg=TEXT_MAIN,
            insertbackground=TEXT_MAIN,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER_COLOR
        )
        name_entry.grid(row=0, column=1, sticky="we", padx=(10, 0), ipady=3)

        tk.Label(
            name_frame,
            text="*留空将自动从 GGUF 文件名提取识别",
            font=("Microsoft YaHei UI", 9),
            bg=CARD_BG,
            fg=TEXT_MUTED
        ).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(2, 0))

        # 5. Context & Port options in one row (Grid row: 10)
        opts_frame = tk.Frame(card, bg=CARD_BG)
        opts_frame.grid(row=10, column=0, columnspan=3, sticky="we", pady=(10, 0))

        tk.Label(opts_frame, text="上下文长度:", font=("Microsoft YaHei UI", 9, "bold"), bg=CARD_BG, fg=TEXT_MAIN).pack(side="left")
        ctx_cb = ttk.Combobox(
            opts_frame,
            textvariable=self.context_var,
            values=["32768 (32K)", "65536 (64K)", "131072 (128K 推荐)", "262144 (256K 全长)"],
            state="readonly",
            width=18
        )
        ctx_cb.pack(side="left", padx=(8, 25))

        tk.Label(opts_frame, text="Web/API 端口:", font=("Microsoft YaHei UI", 9, "bold"), bg=CARD_BG, fg=TEXT_MAIN).pack(side="left")
        port_entry = tk.Entry(
            opts_frame,
            textvariable=self.port_var,
            font=("Microsoft YaHei UI", 9),
            bg=ENTRY_BG,
            fg=TEXT_MAIN,
            insertbackground=TEXT_MAIN,
            width=8,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER_COLOR
        )
        port_entry.pack(side="left", padx=(8, 0))

        # Action Button Frame
        action_frame = tk.Frame(self.root, bg=BG_COLOR, pady=12)
        action_frame.pack(fill="x", padx=25)

        self.import_btn = tk.Button(
            action_frame,
            text="⚡ 开始一键导入与适配",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=ACCENT,
            fg="white",
            activebackground=ACCENT_HOVER,
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=20,
            pady=8,
            command=self._start_import_thread
        )
        self.import_btn.pack(fill="x")

        # Log & Status Card
        log_frame = tk.Frame(self.root, bg=CARD_BG, padx=15, pady=12, highlightbackground=BORDER_COLOR, highlightthickness=1)
        log_frame.pack(fill="both", expand=True, padx=25, pady=(0, 20))

        status_lbl = tk.Label(
            log_frame,
            textvariable=self.status_var,
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=CARD_BG,
            fg="#38bdf8",
            anchor="w"
        )
        status_lbl.pack(fill="x", pady=(0, 6))

        # Log Output Text
        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            bg="#090d16",
            fg="#e2e8f0",
            font=("Consolas", 9),
            relief="flat",
            highlightthickness=0,
            padx=8,
            pady=8
        )
        self.log_text.pack(fill="both", expand=True)

        scroll = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _create_input_row(self, parent, row, label, tip, var, btn_text, btn_cmd, extra_btn_text=None, extra_btn_cmd=None):
        tk.Label(
            parent,
            text=label,
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=CARD_BG,
            fg=TEXT_MAIN
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 2))

        entry = tk.Entry(
            parent,
            textvariable=var,
            font=("Microsoft YaHei UI", 10),
            bg=ENTRY_BG,
            fg=TEXT_MAIN,
            insertbackground=TEXT_MAIN,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER_COLOR
        )
        entry.grid(row=row+1, column=0, sticky="we", ipady=3)
        parent.columnconfigure(0, weight=1)

        btn = tk.Button(
            parent,
            text=btn_text,
            font=("Microsoft YaHei UI", 9),
            bg="#475569",
            fg="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            command=btn_cmd
        )
        btn.grid(row=row+1, column=1, padx=(6, 0))

        if extra_btn_text and extra_btn_cmd:
            extra_btn = tk.Button(
                parent,
                text=extra_btn_text,
                font=("Microsoft YaHei UI", 9),
                bg="#475569",
                fg="white",
                relief="flat",
                cursor="hand2",
                padx=10,
                command=extra_btn_cmd
            )
            extra_btn.grid(row=row+1, column=2, padx=(6, 0))

        tk.Label(
            parent,
            text=tip,
            font=("Microsoft YaHei UI", 8),
            bg=CARD_BG,
            fg=TEXT_MUTED
        ).grid(row=row+2 if not extra_btn_text else row+2, column=0, columnspan=3, sticky="w", pady=(2, 4))

    def _browse_strata_dir(self):
        d = filedialog.askdirectory(title="选择 Strata 项目主目录", initialdir=self.strata_dir_var.get() or str(ROOT.parent))
        if d:
            self.strata_dir_var.set(str(Path(d).resolve()))

    def _browse_model_dir(self):
        d = filedialog.askdirectory(title="选择模型所在的文件夹", initialdir=self.model_path_var.get() or str(ROOT.parent))
        if d:
            self.model_path_var.set(str(Path(d).resolve()))

    def _browse_model_file(self):
        f = filedialog.askopenfilename(
            title="选择主模型 GGUF 文件 (00001-of-00002.gguf 或单文件)",
            filetypes=[("GGUF Files", "*.gguf")],
            initialdir=self.model_path_var.get() or str(ROOT.parent)
        )
        if f:
            self.model_path_var.set(str(Path(f).resolve()))

    def _browse_mtp_file(self):
        f = filedialog.askopenfilename(
            title="选择 MTP 草稿层文件 (mtp-q2_0.gguf)",
            filetypes=[("GGUF Files", "*.gguf")],
            initialdir=self.model_path_var.get() or str(ROOT.parent)
        )
        if f:
            self.mtp_file_var.set(str(Path(f).resolve()))

    def log(self, msg: str):
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")

    def _start_import_thread(self):
        if self.is_running:
            return

        strata_dir = Path(self.strata_dir_var.get().strip().strip('"').strip("'"))
        model_path = Path(self.model_path_var.get().strip().strip('"').strip("'"))

        if not strata_dir.is_dir() or not (strata_dir / "tools" / "iq_pack.py").is_file():
            messagebox.showerror("路径错误", f"所选 Strata 目录无效或缺少 tools/iq_pack.py：\n{strata_dir}")
            return

        if not model_path.exists():
            messagebox.showerror("路径错误", f"所选模型路径不存在：\n{model_path}")
            return

        self.is_running = True
        self.import_btn.config(state="disabled", bg="#64748b", text="⏳ 正在导入与打包，请稍候...")
        self.status_var.set("正在执行导入任务，请勿关闭窗口...")
        self.log_text.delete("1.0", "end")

        t = threading.Thread(target=self._run_import, daemon=True)
        t.start()

    def _run_import(self):
        try:
            strata_dir = Path(self.strata_dir_var.get().strip().strip('"').strip("'")).resolve()
            model_path = Path(self.model_path_var.get().strip().strip('"').strip("'")).resolve()
            tag = self.model_name_var.get().strip()

            # Parse context
            ctx_str = self.context_var.get()
            m = re.search(r"(\d+)", ctx_str)
            ctx = int(m.group(1)) if m else 131072

            # Parse port
            try:
                port = int(self.port_var.get().strip())
            except ValueError:
                port = 8080

            self.log(f"-> Strata 目录: {strata_dir}")
            self.log(f"-> 模型路径: {model_path}")
            self.log(f"-> 目标上下文: {ctx} tokens, 端口: {port}")

            # Run importer.py via subprocess to capture live output
            cmd = [
                sys.executable,
                str(ROOT / "importer.py"),
                "--strata-dir", str(strata_dir),
                "--model-path", str(model_path),
                "--context", str(ctx),
                "--port", str(port)
            ]
            if tag:
                cmd.extend(["--name", tag])
            mtp_custom = self.mtp_file_var.get().strip().strip('"').strip("'")
            if mtp_custom:
                cmd.extend(["--mtp-file", mtp_custom])

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(ROOT)
            )

            created_launcher = None
            for line in proc.stdout:
                line_str = line.rstrip()
                self.log(line_str)
                if "Launcher created:" in line_str:
                    m = re.search(r"Launcher created:\s*(.*)", line_str)
                    if m:
                        created_launcher = m.group(1).strip()

            proc.wait()

            if proc.returncode == 0:
                self.status_var.set("🎉 导入完成！启动脚本已生成！")
                self.log("\n========================================================")
                self.log("✅ 全部步骤完成！你可以直接运行生成的批处理文件。")
                if created_launcher:
                    self.log(f"🚀 启动脚本路径: {created_launcher}")
                self.log("========================================================")
                
                resp = messagebox.askyesno(
                    "导入成功",
                    f"模型已成功适配到 Strata！\n\n启动脚本已生成: {created_launcher or 'run-<name>.bat'}\n\n是否立即启动运行该模型？"
                )
                if resp and created_launcher and Path(created_launcher).is_file():
                    os.startfile(created_launcher)
            else:
                self.status_var.set("❌ 导入失败，请查看上方日志。")
                messagebox.showerror("导入出错", "模型导入过程中出现错误，请检查上方日志详情。")

        except Exception as e:
            self.status_var.set(f"❌ 运行异常: {e}")
            self.log(f"[Exception] {e}")
            messagebox.showerror("异常", str(e))
        finally:
            self.is_running = False
            self.import_btn.config(state="normal", bg=ACCENT, text="⚡ 开始一键导入与适配")


def main():
    root = tk.Tk()
    app = StrataImporterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
