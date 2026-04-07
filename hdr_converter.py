"""
HDR → SDR Converter  •  Plex Edition
RTX / NVENC  •  AV1 / HEVC / H264 input support
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import threading
import os
import json
import re
from pathlib import Path

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".hdr_converter_settings.json")

TONEMAPS   = ["hable", "mobius", "reinhard", "linear", "gamma", "clip"]
PRESETS_NV = {"Качество (медленно)": "p1", "Баланс": "p4", "Скорость": "p7"}
PRESETS_CPU= {"Качество (медленно)": "slow", "Баланс": "medium", "Скорость": "fast"}

ENCODERS = {
    "NVIDIA NVENC H.265 (рекомендуется)": "hevc_nvenc",
    "NVIDIA NVENC H.264":                  "h264_nvenc",
    "CPU H.265 libx265":                   "libx265",
    "CPU H.264 libx264":                   "libx264",
}

CUVID_MAP = {
    "av1":   "av1_cuvid",
    "hevc":  "hevc_cuvid",
    "h264":  "h264_cuvid",
    "vp9":   "vp9_cuvid",
    "mpeg2": "mpeg2_cuvid",
}

AUDIO_OPTS = {
    "Копировать (без изменений)":     "copy",
    "AAC 192k (совместимо с Plex)":   "aac_192",
    "AC3 448k (Dolby Digital)":       "ac3_448",
    "EAC3 640k (Dolby Digital Plus)": "eac3_640",
    "AAC + оригинал (оба потока)":    "aac_plus_orig",
}

CONTAINERS = [".mkv", ".mp4"]
VIDEO_EXTS  = {".mkv", ".mp4", ".m2ts", ".ts", ".avi", ".mov", ".webm"}


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


def probe_codec(ffprobe_path, input_path):
    try:
        r = subprocess.run(
            [ffprobe_path, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )
        return r.stdout.strip().lower()
    except Exception:
        return ""


def build_ffmpeg_cmd(ffmpeg_path, ffprobe_path, input_path, output_path, cfg):
    encoder   = cfg["encoder"]
    use_nvenc = "nvenc" in encoder
    tonemap   = cfg["tonemap"]
    npl       = cfg["npl"]
    desat     = cfg["desat"]
    cq        = cfg["cq"]

    src_codec = probe_codec(ffprobe_path, input_path)
    cuvid_dec = CUVID_MAP.get(src_codec, "")

    cmd = [ffmpeg_path, "-y"]

    # KEY FIX: zscale (tone mapping) is a CPU-only filter.
    # We CANNOT use -hwaccel_output_format cuda together with zscale —
    # that causes "Error reinitialising filters / Invalid argument".
    # Strategy: use GPU decode (av1_cuvid / hevc_cuvid) WITHOUT keeping
    # frames in VRAM. FFmpeg auto-downloads them to RAM before the filter.
    # Then NVENC picks them up for encoding. This is the correct pipeline.
    if use_nvenc and cuvid_dec:
        cmd += ["-hwaccel", "cuda", "-c:v", cuvid_dec]
    elif use_nvenc:
        cmd += ["-hwaccel", "cuda"]

    cmd += ["-i", input_path]

    # Explicit HDR→SDR tone-map filter chain (CPU).
    # Declaring tin/min/pin/rin avoids zscale guessing wrong colorspace.
    vf = (
        "zscale=tin=smpte2084:min=bt2020nc:pin=bt2020:rin=tv"
        f":t=linear:npl={npl},"
        "format=gbrpf32le,"
        "zscale=p=bt709,"
        f"tonemap={tonemap}:desat={desat},"
        "zscale=t=bt709:m=bt709:r=tv,"
        "format=yuv420p"
    )
    cmd += ["-vf", vf]

    # Tag output as SDR so Plex doesn't treat it as HDR
    cmd += [
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-color_range", "tv",
    ]

    # Encoder
    cmd += ["-c:v", encoder]
    if use_nvenc:
        preset = cfg["preset_nv"]
        # rc=vbr + cq gives quality-based encoding (like CRF for CPU)
        cmd += ["-preset", preset, "-rc", "vbr",
                "-cq", str(cq), "-qmin", str(cq), "-qmax", str(cq), "-b:v", "0"]
        if "hevc" in encoder:
            cmd += ["-profile:v", "main", "-tag:v", "hvc1"]
    else:
        cmd += ["-crf", str(cq), "-preset", cfg["preset_cpu"]]

    # Audio
    audio = cfg["audio"]
    if audio == "copy":
        cmd += ["-c:a", "copy"]
    elif audio == "aac_192":
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    elif audio == "ac3_448":
        cmd += ["-c:a", "ac3", "-b:a", "448k"]
    elif audio == "eac3_640":
        cmd += ["-c:a", "eac3", "-b:a", "640k"]
    elif audio == "aac_plus_orig":
        cmd += ["-map", "0:v", "-map", "0:a", "-map", "0:a",
                "-c:a:0", "copy", "-c:a:1", "aac", "-b:a:1", "192k",
                "-disposition:a:0", "default", "-disposition:a:1", "0"]

    # Subtitles
    cmd += ["-c:s", "copy"] if cfg.get("copy_subs") else ["-sn"]

    # Prevent stalls with complex multi-stream files
    cmd += ["-max_muxing_queue_size", "1024"]

    cmd.append(output_path)
    return cmd


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("HDR → SDR Converter  •  Plex Edition")
        self.geometry("920x870")
        self.minsize(860, 740)

        self.settings     = load_settings()
        self._proc        = None
        self._cancel_flag = False

        self._build_ui()
        self._load_from_settings()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        hdr = ctk.CTkFrame(self, fg_color=("#111827", "#0a0f1a"), corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="HDR → SDR Converter",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color="#38bdf8").grid(row=0, column=0, padx=24, pady=(14, 2), sticky="w")
        ctk.CTkLabel(hdr,
                     text="Plex Media Server  •  NVIDIA NVENC  •  AV1 / HEVC / H264",
                     font=ctk.CTkFont(size=11), text_color="#64748b"
                     ).grid(row=1, column=0, padx=24, pady=(0, 12), sticky="w")

        self._build_files(row=1)
        self._build_settings(row=2)
        self._build_paths(row=3)
        self._build_log(row=4)
        self._build_bar(row=5)

    def _section(self, title, row):
        f = ctk.CTkFrame(self, corner_radius=10)
        f.grid(row=row, column=0, sticky="ew", padx=14, pady=(8, 0))
        f.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(f, text=title, font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 4))
        inner = ctk.CTkFrame(f, fg_color="transparent")
        inner.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        inner.grid_columnconfigure(1, weight=1)
        return inner

    def _build_files(self, row):
        p = self._section("📁  Файлы", row)
        p.grid_columnconfigure(1, weight=1)

        self.mode_var = ctk.StringVar(value="single")
        ctk.CTkLabel(p, text="Режим:").grid(row=0, column=0, sticky="w", padx=(4, 8), pady=4)
        mf = ctk.CTkFrame(p, fg_color="transparent")
        mf.grid(row=0, column=1, columnspan=3, sticky="w")
        ctk.CTkRadioButton(mf, text="Один файл", variable=self.mode_var,
                           value="single", command=self._on_mode).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(mf, text="Пакетно (папка)", variable=self.mode_var,
                           value="batch", command=self._on_mode).pack(side="left")

        ctk.CTkLabel(p, text="Вход:").grid(row=1, column=0, sticky="w", padx=(4, 8), pady=4)
        inf = ctk.CTkFrame(p, fg_color="transparent")
        inf.grid(row=1, column=1, columnspan=3, sticky="ew")
        inf.grid_columnconfigure(0, weight=1)
        self.input_var = ctk.StringVar()
        ctk.CTkEntry(inf, textvariable=self.input_var,
                     placeholder_text="Путь к файлу или папке..."
                     ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.btn_browse_in = ctk.CTkButton(inf, text="Обзор", width=80,
                                            command=self._browse_input)
        self.btn_browse_in.grid(row=0, column=1)
        ctk.CTkButton(inf, text="ℹ Инфо", width=70, fg_color="#1e3a5f",
                      hover_color="#1e4976", command=self._probe_file
                      ).grid(row=0, column=2, padx=(6, 0))

        ctk.CTkLabel(p, text="Выход:").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=4)
        outf = ctk.CTkFrame(p, fg_color="transparent")
        outf.grid(row=2, column=1, columnspan=3, sticky="ew")
        outf.grid_columnconfigure(0, weight=1)
        self.output_var = ctk.StringVar()
        ctk.CTkEntry(outf, textvariable=self.output_var,
                     placeholder_text="Папка для сохранения..."
                     ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(outf, text="Обзор", width=80, command=self._browse_output
                      ).grid(row=0, column=1)

        ctk.CTkLabel(p, text="Суффикс:").grid(row=3, column=0, sticky="w", padx=(4, 8), pady=4)
        sf = ctk.CTkFrame(p, fg_color="transparent")
        sf.grid(row=3, column=1, columnspan=3, sticky="w")
        self.suffix_var = ctk.StringVar(value="_SDR")
        ctk.CTkEntry(sf, textvariable=self.suffix_var, width=100).pack(side="left", padx=(0, 16))
        ctk.CTkLabel(sf, text="Контейнер:").pack(side="left", padx=(0, 6))
        self.container_var = ctk.StringVar(value=".mkv")
        ctk.CTkOptionMenu(sf, variable=self.container_var, values=CONTAINERS,
                          width=90).pack(side="left")

    def _build_settings(self, row):
        p = self._section("⚙️  Настройки кодирования", row)

        ctk.CTkLabel(p, text="Кодек:").grid(row=0, column=0, sticky="w", padx=(4, 8), pady=4)
        self.encoder_var = ctk.StringVar(value="NVIDIA NVENC H.265 (рекомендуется)")
        ctk.CTkOptionMenu(p, variable=self.encoder_var, values=list(ENCODERS.keys()),
                          width=280, command=self._on_encoder
                          ).grid(row=0, column=1, sticky="w", pady=4)
        ctk.CTkLabel(p, text="Пресет:").grid(row=0, column=2, sticky="w", padx=(20, 8), pady=4)
        self.preset_var = ctk.StringVar(value="Баланс")
        self.preset_menu = ctk.CTkOptionMenu(p, variable=self.preset_var,
                                              values=list(PRESETS_NV.keys()), width=200)
        self.preset_menu.grid(row=0, column=3, sticky="w", pady=4)

        ctk.CTkLabel(p, text="CQ / CRF:").grid(row=1, column=0, sticky="w", padx=(4, 8), pady=4)
        cqf = ctk.CTkFrame(p, fg_color="transparent")
        cqf.grid(row=1, column=1, sticky="w")
        self.cq_var = ctk.IntVar(value=18)
        self.cq_lbl = ctk.CTkLabel(cqf, text="18", width=30, font=ctk.CTkFont(weight="bold"))
        self.cq_lbl.pack(side="right")
        ctk.CTkSlider(cqf, from_=0, to=35, number_of_steps=35, variable=self.cq_var,
                      width=180, command=lambda v: self.cq_lbl.configure(text=str(int(v)))
                      ).pack(side="left")
        ctk.CTkLabel(p, text="Tone map:", anchor="w").grid(row=1, column=2, sticky="w", padx=(20, 8), pady=4)
        self.tonemap_var = ctk.StringVar(value="hable")
        ctk.CTkOptionMenu(p, variable=self.tonemap_var, values=TONEMAPS,
                          width=130).grid(row=1, column=3, sticky="w", pady=4)

        ctk.CTkLabel(p, text="Пик (npl):").grid(row=2, column=0, sticky="w", padx=(4, 8), pady=4)
        nplf = ctk.CTkFrame(p, fg_color="transparent")
        nplf.grid(row=2, column=1, sticky="w")
        self.npl_var = ctk.IntVar(value=100)
        self.npl_lbl = ctk.CTkLabel(nplf, text="100 нит", width=65)
        self.npl_lbl.pack(side="right")
        ctk.CTkSlider(nplf, from_=50, to=1000, number_of_steps=38, variable=self.npl_var,
                      width=180, command=lambda v: self.npl_lbl.configure(text=f"{int(v)} нит")
                      ).pack(side="left")
        ctk.CTkLabel(p, text="Desat:", anchor="w").grid(row=2, column=2, sticky="w", padx=(20, 8), pady=4)
        dsf = ctk.CTkFrame(p, fg_color="transparent")
        dsf.grid(row=2, column=3, sticky="w")
        self.desat_var = ctk.DoubleVar(value=0.0)
        self.desat_lbl = ctk.CTkLabel(dsf, text="0.0", width=30)
        self.desat_lbl.pack(side="right")
        ctk.CTkSlider(dsf, from_=0, to=1, number_of_steps=20, variable=self.desat_var,
                      width=130, command=lambda v: self.desat_lbl.configure(text=f"{v:.1f}")
                      ).pack(side="left")

        ctk.CTkLabel(p, text="Аудио:").grid(row=3, column=0, sticky="w", padx=(4, 8), pady=4)
        self.audio_var = ctk.StringVar(value="Копировать (без изменений)")
        ctk.CTkOptionMenu(p, variable=self.audio_var, values=list(AUDIO_OPTS.keys()),
                          width=300).grid(row=3, column=1, columnspan=3, sticky="w", pady=4)

        chkf = ctk.CTkFrame(p, fg_color="transparent")
        chkf.grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.copy_subs_var = ctk.BooleanVar(value=True)
        self.overwrite_var  = ctk.BooleanVar(value=False)
        self.shutdown_var   = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(chkf, text="Копировать субтитры",
                        variable=self.copy_subs_var).pack(side="left", padx=(4, 20))
        ctk.CTkCheckBox(chkf, text="Перезаписывать существующие",
                        variable=self.overwrite_var).pack(side="left", padx=(0, 20))
        ctk.CTkCheckBox(chkf, text="Выключить ПК после",
                        variable=self.shutdown_var).pack(side="left")

    def _build_paths(self, row):
        p = self._section("🔧  Пути к FFmpeg", row)
        p.grid_columnconfigure(1, weight=1)

        for r_idx, (lbl, attr) in enumerate([("ffmpeg:", "ffmpeg_var"), ("ffprobe:", "ffprobe_var")]):
            ctk.CTkLabel(p, text=lbl).grid(row=r_idx, column=0, sticky="w", padx=(4, 8), pady=4)
            row_f = ctk.CTkFrame(p, fg_color="transparent")
            row_f.grid(row=r_idx, column=1, sticky="ew")
            row_f.grid_columnconfigure(0, weight=1)
            default = self.settings.get(
                "ffmpeg_path" if attr == "ffmpeg_var" else "ffprobe_path",
                r"C:\ffmpeg\bin\ffmpeg.exe" if attr == "ffmpeg_var" else r"C:\ffmpeg\bin\ffprobe.exe"
            )
            setattr(self, attr, ctk.StringVar(value=default))
            ctk.CTkEntry(row_f, textvariable=getattr(self, attr)
                         ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
            browse_cmd = self._browse_ffmpeg if attr == "ffmpeg_var" else self._browse_ffprobe
            ctk.CTkButton(row_f, text="Обзор", width=70, command=browse_cmd
                          ).grid(row=0, column=1)
            if attr == "ffmpeg_var":
                ctk.CTkButton(row_f, text="Проверить", width=100,
                              fg_color="#1e3a5f", hover_color="#1e4976",
                              command=self._check_ffmpeg
                              ).grid(row=0, column=2, padx=(6, 0))

    def _build_log(self, row):
        f = ctk.CTkFrame(self, corner_radius=10)
        f.grid(row=row, column=0, sticky="nsew", padx=14, pady=(8, 0))
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)
        hf = ctk.CTkFrame(f, fg_color="transparent")
        hf.grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 4))
        ctk.CTkLabel(hf, text="📋  Лог",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkButton(hf, text="Очистить", width=80, height=24,
                      fg_color="#374151", hover_color="#4b5563",
                      command=self._clear_log).pack(side="right")
        self.log_box = ctk.CTkTextbox(f, font=ctk.CTkFont(family="Consolas", size=11),
                                      state="disabled", wrap="word")
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

    def _build_bar(self, row):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=row, column=0, sticky="ew", padx=14, pady=(6, 14))
        bar.grid_columnconfigure(2, weight=1)

        self.progress = ctk.CTkProgressBar(bar, height=12)
        self.progress.grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 8))
        self.progress.set(0)

        self.btn_convert = ctk.CTkButton(
            bar, text="▶  Конвертировать", width=190, height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#0369a1", hover_color="#0284c7", command=self._start)
        self.btn_convert.grid(row=1, column=0, padx=(0, 8))

        self.btn_cancel = ctk.CTkButton(
            bar, text="⏹  Стоп", width=110, height=42,
            fg_color="#991b1b", hover_color="#b91c1c",
            state="disabled", command=self._cancel)
        self.btn_cancel.grid(row=1, column=1)

        self.status_lbl = ctk.CTkLabel(bar, text="Готов",
                                        font=ctk.CTkFont(size=12), text_color="#64748b")
        self.status_lbl.grid(row=1, column=2, sticky="w", padx=12)

        ctk.CTkButton(bar, text="Показать команду", width=160, height=42,
                      fg_color="#374151", hover_color="#4b5563",
                      command=self._show_cmd).grid(row=1, column=3, padx=(0, 8))

        ctk.CTkButton(bar, text="💾 Сохранить настройки", width=180, height=42,
                      fg_color="#1e3a5f", hover_color="#1e4976",
                      command=self._save_all).grid(row=1, column=4)

    # ── Events ────────────────────────────────────────────────────────────────
    def _on_mode(self):
        self.input_var.set("")
        self.btn_browse_in.configure(
            text="Папка" if self.mode_var.get() == "batch" else "Обзор")

    def _on_encoder(self, _=None):
        is_nv = "NVENC" in self.encoder_var.get()
        vals = list(PRESETS_NV.keys()) if is_nv else list(PRESETS_CPU.keys())
        self.preset_menu.configure(values=vals)
        if self.preset_var.get() not in vals:
            self.preset_var.set("Баланс")

    def _browse_input(self):
        if self.mode_var.get() == "single":
            p = filedialog.askopenfilename(
                filetypes=[("Видео", "*.mkv *.mp4 *.m2ts *.ts *.avi *.mov *.webm"), ("Все", "*.*")])
        else:
            p = filedialog.askdirectory()
        if p:
            self.input_var.set(p)
            if not self.output_var.get():
                self.output_var.set(str(Path(p).parent if self.mode_var.get() == "single" else p))

    def _browse_output(self):
        p = filedialog.askdirectory()
        if p:
            self.output_var.set(p)

    def _browse_ffmpeg(self):
        p = filedialog.askopenfilename(filetypes=[("Exe", "*.exe"), ("Все", "*.*")])
        if p:
            self.ffmpeg_var.set(p)

    def _browse_ffprobe(self):
        p = filedialog.askopenfilename(filetypes=[("Exe", "*.exe"), ("Все", "*.*")])
        if p:
            self.ffprobe_var.set(p)

    def _check_ffmpeg(self):
        path = self.ffmpeg_var.get()
        try:
            r = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=5,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            ver = r.stdout.split("\n")[0]
            r2 = subprocess.run([path, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            r3 = subprocess.run([path, "-hide_banner", "-decoders"], capture_output=True, text=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            nvenc   = "hevc_nvenc"  in r2.stdout
            av1_dec = "av1_cuvid"   in r3.stdout
            hevc_dec= "hevc_cuvid"  in r3.stdout
            self._log(f"✅ {ver}")
            self._log(f"   NVENC (GPU encode):   {'✅' if nvenc    else '❌'}")
            self._log(f"   AV1 CUVID (GPU decode):{'✅' if av1_dec  else '⚠️  будет CPU decode'}")
            self._log(f"   HEVC CUVID (GPU decode):{'✅' if hevc_dec else '⚠️  будет CPU decode'}")
        except Exception as e:
            self._log(f"❌ ffmpeg не найден: {e}")

    def _probe_file(self):
        inp = self.input_var.get().strip()
        if not inp or not os.path.isfile(inp):
            messagebox.showinfo("Инфо", "Укажите существующий файл")
            return
        try:
            r = subprocess.run(
                [self.ffprobe_var.get(), "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-show_format", inp],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            data = json.loads(r.stdout)
            self._log("\n=== Информация о файле ===")
            for s in data.get("streams", []):
                t = s.get("codec_type", "")
                if t == "video":
                    ct = s.get("color_transfer", "")
                    is_hdr = ct in ("smpte2084", "arib-std-b67", "smpte428")
                    self._log(f"  Видео: {s.get('codec_name','?').upper()}  "
                              f"{s.get('width','?')}x{s.get('height','?')}  "
                              f"{s.get('r_frame_rate','?')} fps")
                    self._log(f"    Transfer: {ct}  |  Space: {s.get('color_space','?')}  "
                              f"|  Primaries: {s.get('color_primaries','?')}")
                    self._log(f"    HDR: {'✅ Да (' + ct + ')' if is_hdr else '❌ Нет (SDR)'}")
                elif t == "audio":
                    self._log(f"  Аудио: {s.get('codec_name','?')}  "
                              f"{s.get('channel_layout','?')}  "
                              f"{s.get('sample_rate','?')}Hz")
                elif t == "subtitle":
                    lang = s.get("tags", {}).get("language", "?")
                    self._log(f"  Субтитры: {s.get('codec_name','?')}  [{lang}]")
            fmt = data.get("format", {})
            dur  = float(fmt.get("duration", 0))
            size = int(fmt.get("size", 0)) / (1024**3)
            self._log(f"  Длительность: {int(dur//3600)}ч {int((dur%3600)//60)}м {int(dur%60)}с  |  "
                      f"Размер: {size:.2f} ГБ")
        except Exception as e:
            self._log(f"❌ ffprobe ошибка: {e}")

    def _get_config(self):
        enc = self.encoder_var.get()
        preset = self.preset_var.get()
        return {
            "encoder":    ENCODERS[enc],
            "preset_nv":  PRESETS_NV.get(preset, "p4"),
            "preset_cpu": PRESETS_CPU.get(preset, "medium"),
            "cq":         self.cq_var.get(),
            "tonemap":    self.tonemap_var.get(),
            "npl":        self.npl_var.get(),
            "desat":      round(self.desat_var.get(), 1),
            "audio":      AUDIO_OPTS[self.audio_var.get()],
            "copy_subs":  self.copy_subs_var.get(),
        }

    def _make_out(self, inp):
        p = Path(inp)
        od = Path(self.output_var.get()) if self.output_var.get() else p.parent
        return str(od / f"{p.stem}{self.suffix_var.get()}{self.container_var.get()}")

    def _show_cmd(self):
        inp = self.input_var.get() or "input.mkv"
        out = self._make_out(inp)
        cmd = build_ffmpeg_cmd(self.ffmpeg_var.get(), self.ffprobe_var.get(),
                               inp, out, self._get_config())
        win = ctk.CTkToplevel(self)
        win.title("FFmpeg команда")
        win.geometry("860x220")
        win.grab_set()
        tb = ctk.CTkTextbox(win, font=ctk.CTkFont(family="Consolas", size=11), wrap="word")
        tb.pack(fill="both", expand=True, padx=10, pady=10)
        tb.insert("end", " ".join(f'"{a}"' if " " in a else a for a in cmd))
        tb.configure(state="disabled")

    # ── Log ───────────────────────────────────────────────────────────────────
    def _log(self, text):
        def _do():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(0, _do)

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _set_status(self, text):
        self.after(0, lambda: self.status_lbl.configure(text=text))

    # ── Conversion ────────────────────────────────────────────────────────────
    def _collect(self):
        inp = self.input_var.get().strip()
        if not inp:
            messagebox.showerror("Ошибка", "Укажите входной файл или папку."); return []
        if self.mode_var.get() == "single":
            if not os.path.isfile(inp):
                messagebox.showerror("Ошибка", f"Файл не найден:\n{inp}"); return []
            return [inp]
        if not os.path.isdir(inp):
            messagebox.showerror("Ошибка", f"Папка не найдена:\n{inp}"); return []
        files = sorted(str(f) for f in Path(inp).rglob("*") if f.suffix.lower() in VIDEO_EXTS)
        if not files:
            messagebox.showerror("Ошибка", "В папке не найдено видеофайлов."); return []
        return files

    def _start(self):
        files = self._collect()
        if not files:
            return
        od = self.output_var.get().strip()
        if od:
            os.makedirs(od, exist_ok=True)
        self._cancel_flag = False
        self.btn_convert.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.after(0, lambda: self.progress.set(0))
        self._clear_log()
        threading.Thread(target=self._run, args=(files,), daemon=True).start()

    def _cancel(self):
        self._cancel_flag = True
        if self._proc:
            try: self._proc.terminate()
            except Exception: pass
        self._log("⏹ Отменено")
        self._set_status("Отменено")

    def _run(self, files):
        total   = len(files)
        cfg     = self._get_config()
        ffmpeg  = self.ffmpeg_var.get()
        ffprobe = self.ffprobe_var.get()

        for idx, inp in enumerate(files, 1):
            if self._cancel_flag:
                break
            out = self._make_out(inp)
            if not self.overwrite_var.get() and os.path.exists(out):
                self._log(f"⏭ Пропуск: {Path(out).name}"); continue

            codec = probe_codec(ffprobe, inp)
            self._log(f"\n[{idx}/{total}] {Path(inp).name}  [{codec.upper() or '?'}]")
            self._log(f"   → {Path(out).name}")
            self._set_status(f"Конвертирую {idx}/{total}…")

            cmd = build_ffmpeg_cmd(ffmpeg, ffprobe, inp, out, cfg)

            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )
                fps_re   = re.compile(r"fps=\s*([\d.]+)")
                speed_re = re.compile(r"speed=\s*([\d.x]+)")
                time_re  = re.compile(r"time=(\d+):(\d+):([\d.]+)")
                err_re   = re.compile(r"\b(Error|error|Invalid|failed|Cannot)\b")

                for line in self._proc.stdout:
                    line = line.rstrip()
                    if "frame=" in line:
                        parts = []
                        m = fps_re.search(line)
                        if m: parts.append(f"fps={m.group(1)}")
                        m = speed_re.search(line)
                        if m: parts.append(f"скорость={m.group(1)}")
                        m = time_re.search(line)
                        if m:
                            h, mn, s = m.groups()
                            parts.append(f"время={h}:{mn}:{float(s):.0f}s")
                        if parts:
                            self._set_status("  ".join(parts))
                    elif err_re.search(line) and "warning" not in line.lower():
                        self._log("  ⚠ " + line)

                self._proc.wait()
                rc = self._proc.returncode
                if rc == 0:
                    size = os.path.getsize(out) / (1024**3)
                    self._log(f"  ✅ Готово  ({size:.2f} ГБ)")
                else:
                    self._log(f"  ❌ Ошибка (код {rc})")
                    self._log("     → Нажмите «Показать команду» и проверьте вручную в терминале")
            except FileNotFoundError:
                self._log(f"  ❌ ffmpeg не найден: {ffmpeg}"); break
            except Exception as e:
                self._log(f"  ❌ {e}")

            self.after(0, self.progress.set, idx / total)

        self.after(0, self._done)

    def _done(self):
        self.btn_convert.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self._set_status("Готов")
        if not self._cancel_flag:
            self._log("\n🏁 Все файлы обработаны!")
            if self.shutdown_var.get():
                self._log("⚠ Выключение через 60 секунд…")
                subprocess.run(["shutdown", "/s", "/t", "60"])

    # ── Persist ───────────────────────────────────────────────────────────────
    def _save_all(self):
        save_settings({
            "ffmpeg_path":  self.ffmpeg_var.get(),
            "ffprobe_path": self.ffprobe_var.get(),
            "encoder":      self.encoder_var.get(),
            "preset":       self.preset_var.get(),
            "cq":           self.cq_var.get(),
            "tonemap":      self.tonemap_var.get(),
            "npl":          self.npl_var.get(),
            "desat":        round(self.desat_var.get(), 1),
            "audio":        self.audio_var.get(),
            "suffix":       self.suffix_var.get(),
            "container":    self.container_var.get(),
        })
        self._log("💾 Настройки сохранены")

    def _load_from_settings(self):
        s = self.settings
        for key, attr, cast in [
            ("ffmpeg_path",  "ffmpeg_var",  str),
            ("ffprobe_path", "ffprobe_var", str),
            ("suffix",       "suffix_var",  str),
            ("container",    "container_var", str),
            ("tonemap",      "tonemap_var", str),
            ("audio",        "audio_var",   str),
            ("cq",           "cq_var",      int),
            ("npl",          "npl_var",     int),
            ("desat",        "desat_var",   float),
        ]:
            if key in s:
                getattr(self, attr).set(cast(s[key]))
        if "encoder" in s and s["encoder"] in ENCODERS:
            self.encoder_var.set(s["encoder"])
            self._on_encoder()
        if "preset" in s:
            self.preset_var.set(s["preset"])
        # Sync slider labels
        self.cq_lbl.configure(text=str(self.cq_var.get()))
        self.npl_lbl.configure(text=f"{self.npl_var.get()} нит")
        self.desat_lbl.configure(text=f"{self.desat_var.get():.1f}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
