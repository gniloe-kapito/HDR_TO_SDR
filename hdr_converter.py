"""
HDR → SDR Converter для Plex
Красивый, понятный интерфейс с подсказками
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess, threading, os, json, re, shutil, time
from pathlib import Path

# ── Тема ──────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Цвета приложения
C = {
    "bg":          "#06111b",
    "surface":     "#0b1a29",
    "surface_alt": "#102439",
    "card":        "#102133",
    "card_alt":    "#16304a",
    "border":      "#24435f",
    "accent":      "#56d6c3",
    "accent2":     "#f7b955",
    "accent_soft": "#1c665f",
    "green":       "#69e18b",
    "red":         "#ff7676",
    "yellow":      "#f7b955",
    "text":        "#ecf5ff",
    "muted":       "#9eb4c9",
    "dim":         "#5d7792",
}

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".hdr_sdr_v2.json")
DEFAULT_FFMPEG = shutil.which("ffmpeg") or r"C:\ffmpeg\bin\ffmpeg.exe"
DEFAULT_FFPROBE = shutil.which("ffprobe") or r"C:\ffmpeg\bin\ffprobe.exe"

# ── Константы ──────────────────────────────────────────────────────────────────
TONEMAP_OPTIONS = {
    "Hable (рекомендуется)":   "hable",
    "Reinhard (ярче)":         "reinhard",
    "Mobius (мягкий)":         "mobius",
    "Linear (без обработки)":  "linear",
}

ENCODER_OPTIONS = {
    "NVIDIA GeForce (H.265) — быстро, отличное качество": "hevc_nvenc",
    "NVIDIA GeForce (H.264) — совместимость со старыми ТВ": "h264_nvenc",
    "Процессор (H.265) — если нет NVIDIA":               "libx265",
    "Процессор (H.264) — медленно, максимум совместимость": "libx264",
}

SPEED_OPTIONS = {
    "Турбо":    {"nv": "p1", "cpu": "faster"},
    "Баланс":   {"nv": "p2", "cpu": "medium"},
    "Качество": {"nv": "p5", "cpu": "slow"},
}

AUDIO_OPTIONS = {
    "Оставить как есть (рекомендуется)":          "copy",
    "Конвертировать в AAC — для старых устройств": "aac_192",
    "Конвертировать в AC3 (Dolby Digital)":        "ac3_448",
    "Оставить оригинал + добавить AAC дорожку":    "aac_plus_orig",
}

PROCESSING_OPTIONS = {
    "Авто (GPU Vulkan/libplacebo, рекомендуется)": "auto",
    "GPU Vulkan / libplacebo — максимум скорости": "libplacebo",
    "GPU OpenCL — совместимый запасной режим":     "opencl",
    "CPU zscale + tonemap — самый совместимый":    "cpu",
}

PIPELINE_LABELS = {
    "libplacebo": "GPU Vulkan/libplacebo",
    "opencl":     "GPU OpenCL",
    "cpu":        "CPU zscale/tonemap",
}

ENCODER_LABELS = {
    "hevc_nvenc": "NVENC H.265",
    "h264_nvenc": "NVENC H.264",
    "libx265":    "CPU H.265",
    "libx264":    "CPU H.264",
}

CUVID_MAP = {"av1":"av1_cuvid","hevc":"hevc_cuvid","h264":"h264_cuvid","vp9":"vp9_cuvid"}
VIDEO_EXTS = {".mkv",".mp4",".m2ts",".ts",".avi",".mov",".webm"}
FFMPEG_INFO_CACHE = {}


# ── Утилиты ───────────────────────────────────────────────────────────────────
def load_settings():
    try:
        with open(SETTINGS_FILE) as f: return json.load(f)
    except: return {}

def save_settings(d):
    try:
        with open(SETTINGS_FILE,"w") as f: json.dump(d,f,indent=2)
    except: pass

def probe_codec(ffprobe, inp):
    try:
        r = subprocess.run(
            [ffprobe,"-v","error","-select_streams","v:0",
             "-show_entries","stream=codec_name",
             "-of","default=noprint_wrappers=1:nokey=1",inp],
            capture_output=True,text=True,timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
        return r.stdout.strip().lower()
    except: return ""

def probe_info(ffprobe, inp):
    try:
        r = subprocess.run(
            [ffprobe,"-v","quiet","-print_format","json",
             "-show_streams","-show_format",inp],
            capture_output=True,text=True,timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
        return json.loads(r.stdout)
    except: return {}

def probe_duration(ffprobe, inp):
    data = probe_info(ffprobe, inp)
    if not data:
        return 0.0
    try:
        dur = float(data.get("format", {}).get("duration", 0) or 0)
        if dur > 0:
            return dur
    except:
        pass
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            try:
                dur = float(stream.get("duration", 0) or 0)
                if dur > 0:
                    return dur
            except:
                pass
    return 0.0

def format_clock(seconds):
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def ffmpeg_query(ffmpeg, *args, timeout=15):
    try:
        r = subprocess.run(
            [ffmpeg, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0,
        )
        return (r.stdout or "") + (r.stderr or "")
    except:
        return ""

def inspect_ffmpeg(ffmpeg):
    key = os.path.normcase(os.path.abspath(ffmpeg))
    if key in FFMPEG_INFO_CACHE:
        return FFMPEG_INFO_CACHE[key]

    version = ffmpeg_query(ffmpeg, "-version", timeout=5)
    encoders = ffmpeg_query(ffmpeg, "-hide_banner", "-encoders")
    decoders = ffmpeg_query(ffmpeg, "-hide_banner", "-decoders")
    filters  = ffmpeg_query(ffmpeg, "-hide_banner", "-filters")
    hwaccels = ffmpeg_query(ffmpeg, "-hide_banner", "-hwaccels")

    info = {
        "version": version.splitlines()[0].strip() if version.strip() else "",
        "nvenc_hevc": "hevc_nvenc" in encoders,
        "nvenc_h264": "h264_nvenc" in encoders,
        "libplacebo": "libplacebo" in filters,
        "tonemap_opencl": "tonemap_opencl" in filters,
        "vulkan": bool(re.search(r"(?mi)^\s*vulkan\s*$", hwaccels)),
        "opencl": bool(re.search(r"(?mi)^\s*opencl\s*$", hwaccels)),
        "cuda": bool(re.search(r"(?mi)^\s*cuda\s*$", hwaccels)),
        "cuvid": {codec: decoder in decoders for codec, decoder in CUVID_MAP.items()},
    }
    FFMPEG_INFO_CACHE[key] = info
    return info

def resolve_processing_backend(requested, caps):
    libplacebo_ok = caps["libplacebo"] and caps["vulkan"]
    opencl_ok = caps["tonemap_opencl"] and caps["opencl"]

    if requested == "libplacebo":
        if libplacebo_ok: return "libplacebo"
        if opencl_ok: return "opencl"
        return "cpu"
    if requested == "opencl":
        if opencl_ok: return "opencl"
        if libplacebo_ok: return "libplacebo"
        return "cpu"
    if requested == "cpu":
        return "cpu"
    if libplacebo_ok:
        return "libplacebo"
    if opencl_ok:
        return "opencl"
    return "cpu"

def libplacebo_brightness(npl):
    boost = (npl - 100) / 900 * 0.25
    return round(max(-0.15, min(0.25, boost)), 3)

def libplacebo_saturation(desat):
    return round(max(0.7, 1.0 - desat * 0.3), 2)

def build_job(ffmpeg, ffprobe, inp, out, cfg):
    encoder   = cfg["encoder"]
    use_nvenc = "nvenc" in encoder
    src_codec = probe_codec(ffprobe, inp)
    caps      = inspect_ffmpeg(ffmpeg)
    backend   = resolve_processing_backend(cfg.get("processing", "auto"), caps)
    cuvid_dec = CUVID_MAP.get(src_codec, "") if caps["cuvid"].get(src_codec) else ""
    use_cuda_decode = bool(cuvid_dec) and backend in ("cpu", "libplacebo")

    npl    = cfg["npl"]
    tmap   = cfg["tonemap"]
    desat  = cfg["desat"]
    cq     = cfg["cq"]

    cmd = [ffmpeg, "-y"]
    if backend == "libplacebo":
        cmd += ["-init_hw_device","vulkan=vk","-filter_hw_device","vk"]
        if use_cuda_decode:
            cmd += ["-hwaccel","cuda","-hwaccel_output_format","cuda",
                    "-extra_hw_frames","8","-c:v",cuvid_dec]
    elif backend == "opencl":
        cmd += ["-init_hw_device","opencl=ocl","-filter_hw_device","ocl"]
    elif use_cuda_decode:
        cmd += ["-hwaccel","cuda","-c:v",cuvid_dec]
    elif use_nvenc and caps["cuda"]:
        cmd += ["-hwaccel","cuda"]

    cmd += ["-i", inp]

    if backend == "libplacebo":
        prefix = "hwdownload,format=p010le," if use_cuda_decode else ""
        vf = (
            f"{prefix}libplacebo="
            f"tonemapping={tmap}:peak_detect=true:contrast_recovery=0.3:"
            "colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:"
            f"format=yuv420p:brightness={libplacebo_brightness(npl)}:"
            f"saturation={libplacebo_saturation(desat)}"
        )
    elif backend == "opencl":
        vf = (
            "format=p010le,"
            "hwupload=derive_device=opencl,"
            f"tonemap_opencl=tonemap={tmap}:desat={desat}:threshold=0.2:"
            "format=nv12:p=bt709:t=bt709:m=bt709:r=tv,"
            "hwdownload,format=nv12"
        )
    else:
        vf = (
            "zscale=tin=smpte2084:min=bt2020nc:pin=bt2020:rin=tv"
            f":t=linear:npl={npl},"
            "format=gbrpf32le,"
            "zscale=p=bt709,"
            f"tonemap={tmap}:desat={desat},"
            "zscale=t=bt709:m=bt709:r=tv,"
            "format=yuv420p"
        )
    cmd += ["-vf", vf]
    cmd += ["-colorspace","bt709","-color_primaries","bt709",
            "-color_trc","bt709","-color_range","tv"]

    cmd += ["-c:v", encoder]
    if use_nvenc:
        spd = cfg["speed_nv"]
        cmd += ["-preset",spd,"-tune","hq","-multipass","disabled",
                "-rc","vbr","-cq",str(cq),"-b:v","0"]
        if "hevc" in encoder:
            cmd += ["-profile:v","main","-tag:v","hvc1"]
    else:
        cmd += ["-crf",str(cq),"-preset",cfg["speed_cpu"]]

    audio = cfg["audio"]
    if audio=="copy":              cmd += ["-c:a","copy"]
    elif audio=="aac_192":         cmd += ["-c:a","aac","-b:a","192k"]
    elif audio=="ac3_448":         cmd += ["-c:a","ac3","-b:a","448k"]
    elif audio=="aac_plus_orig":
        cmd += ["-map","0:v","-map","0:a","-map","0:a",
                "-c:a:0","copy","-c:a:1","aac","-b:a:1","192k",
                "-disposition:a:0","default","-disposition:a:1","0"]

    cmd += ["-c:s","copy"] if cfg.get("copy_subs") else ["-sn"]
    cmd += ["-max_muxing_queue_size","1024"]
    cmd.append(out)
    return {
        "cmd": cmd,
        "backend": backend,
        "src_codec": src_codec,
        "cuda_decode": use_cuda_decode,
        "encoder_label": ENCODER_LABELS.get(encoder, encoder),
        "pipeline_label": PIPELINE_LABELS[backend],
        "fallback": backend != cfg.get("processing", "auto") and cfg.get("processing", "auto") != "auto",
    }

def build_cmd(ffmpeg, ffprobe, inp, out, cfg):
    return build_job(ffmpeg, ffprobe, inp, out, cfg)["cmd"]


# ── Tooltip ───────────────────────────────────────────────────────────────────
class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text   = text
        self.tw     = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _=None):
        if self.tw: return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tw = tk.Toplevel(self.widget)
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(self.tw, text=self.text, justify="left",
                       background="#21262d", foreground="#e6edf3",
                       relief="flat", bd=0, padx=10, pady=6,
                       font=("Segoe UI", 9), wraplength=320)
        lbl.pack()
        # border frame
        self.tw.configure(background="#30363d")

    def hide(self, _=None):
        if self.tw:
            self.tw.destroy()
            self.tw = None


# ── Виджет: карточка-настройка ─────────────────────────────────────────────
class SettingRow(ctk.CTkFrame):
    """Строка с иконкой, заголовком, подписью и виджетом справа."""
    def __init__(self, parent, icon, title, subtitle, widget_factory, tooltip=None, **kw):
        super().__init__(parent, fg_color="transparent", **kw)
        self.grid_columnconfigure(1, weight=1)

        # Иконка
        ctk.CTkLabel(self, text=icon, font=ctk.CTkFont(size=20),
                     width=36, text_color=C["accent"]
                     ).grid(row=0, column=0, rowspan=2, padx=(0,12), pady=6, sticky="n")

        # Текст
        tf = ctk.CTkFrame(self, fg_color="transparent")
        tf.grid(row=0, column=1, sticky="ew")
        title_lbl = ctk.CTkLabel(tf, text=title,
                                  font=ctk.CTkFont(size=13, weight="bold"),
                                  text_color=C["text"], anchor="w")
        title_lbl.pack(side="left")
        if tooltip:
            help_lbl = ctk.CTkLabel(tf, text=" (?)",
                                     font=ctk.CTkFont(size=11),
                                     text_color=C["accent"], cursor="question_arrow")
            help_lbl.pack(side="left")
            Tooltip(help_lbl, tooltip)

        ctk.CTkLabel(self, text=subtitle, font=ctk.CTkFont(size=11),
                     text_color=C["muted"], anchor="w"
                     ).grid(row=1, column=1, sticky="w")

        # Виджет
        w = widget_factory(self)
        w.grid(row=0, column=2, rowspan=2, padx=(12,0), sticky="e")

        # Разделитель
        sep = ctk.CTkFrame(self, height=1, fg_color=C["border"])
        sep.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8,0))


# ── Главное приложение ────────────────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("HDR → SDR  •  для Plex")
        self.geometry("920x760")
        self.minsize(760, 620)
        self.configure(fg_color=C["bg"])

        self.settings     = load_settings()
        self._proc        = None
        self._cancel_flag = False

        self._init_vars()
        self._build()
        self._load_settings()

    # ── Переменные ────────────────────────────────────────────────────────────
    def _init_vars(self):
        self.v_mode      = ctk.StringVar(value="single")
        self.v_input     = ctk.StringVar()
        self.v_output    = ctk.StringVar()
        self.v_suffix    = ctk.StringVar(value="_SDR")
        self.v_container = ctk.StringVar(value=".mkv")
        self.v_encoder   = ctk.StringVar(value=list(ENCODER_OPTIONS)[0])
        self.v_processing = ctk.StringVar(value=list(PROCESSING_OPTIONS)[0])
        self.v_speed     = ctk.StringVar(value=list(SPEED_OPTIONS)[1])
        self.v_quality   = ctk.IntVar(value=18)
        self.v_tonemap   = ctk.StringVar(value=list(TONEMAP_OPTIONS)[0])
        self.v_brightness = ctk.IntVar(value=100)
        self.v_saturation = ctk.DoubleVar(value=0.0)
        self.v_audio     = ctk.StringVar(value=list(AUDIO_OPTIONS)[0])
        self.v_subs      = ctk.BooleanVar(value=True)
        self.v_overwrite = ctk.BooleanVar(value=False)
        self.v_shutdown  = ctk.BooleanVar(value=False)
        self.v_ffmpeg    = ctk.StringVar(value=DEFAULT_FFMPEG)
        self.v_ffprobe   = ctk.StringVar(value=DEFAULT_FFPROBE)

    # ── Построение UI ─────────────────────────────────────────────────────────
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Вкладки
        self.tabs = ctk.CTkTabview(self, fg_color=C["surface"],
                                    segmented_button_fg_color=C["card"],
                                    segmented_button_selected_color=C["accent"],
                                    segmented_button_selected_hover_color=C["accent_soft"],
                                    segmented_button_unselected_color=C["card"],
                                    segmented_button_unselected_hover_color=C["border"],
                                    border_color=C["border"], border_width=1)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=16, pady=(16,8))

        self.tab_names = ["📂  Файлы", "🎨  Качество", "🔊  Аудио", "⚙️  Настройки"]
        for name in self.tab_names:
            self.tabs.add(name)
        self.tab_bodies = {name: self._create_tab_body(name) for name in self.tab_names}

        self._build_tab_files()
        self._build_tab_quality()
        self._build_tab_audio()
        self._build_tab_settings()

        self._build_bottom()

    def _create_tab_body(self, name):
        tab = self.tabs.tab(name)
        tab.configure(fg_color="transparent")
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(
            tab,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_fg_color="transparent",
            scrollbar_button_color=C["border"],
            scrollbar_button_hover_color=C["accent_soft"],
        )
        body.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        body.grid_columnconfigure(0, weight=1)
        return body

    # ── Вкладка: Файлы ────────────────────────────────────────────────────────
    def _build_tab_files(self):
        tab = self.tab_bodies["📂  Файлы"]
        tab.grid_columnconfigure(0, weight=1)

        # --- Режим ---
        mode_card = self._card(tab, "Режим работы", row=0)
        mode_card.grid_columnconfigure(0, weight=1)
        mode_card.grid_columnconfigure(1, weight=1)

        self._radio_card(mode_card, "🎬  Один фильм",
                         "Конвертировать один выбранный файл",
                         "single", col=0)
        self._radio_card(mode_card, "📁  Целая папка",
                         "Конвертировать все видео в папке разом",
                         "batch", col=1)

        # --- Вход ---
        in_card = self._card(tab, "Исходный файл / папка с фильмами", row=1)
        in_card.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(in_card, textvariable=self.v_input, height=38,
                     placeholder_text="Нажмите «Выбрать» или перетащите файл...",
                     font=ctk.CTkFont(size=12),
                     fg_color=C["bg"], border_color=C["border"]
                     ).grid(row=0, column=0, sticky="ew", padx=(0,8))
        btn_f = ctk.CTkFrame(in_card, fg_color="transparent")
        btn_f.grid(row=0, column=1)
        ctk.CTkButton(btn_f, text="Выбрать", width=90, height=38,
                      command=self._browse_in).pack(side="left", padx=(0,6))
        ctk.CTkButton(btn_f, text="ℹ", width=38, height=38,
                      fg_color=C["card"], hover_color=C["border"],
                      command=self._probe,
                      font=ctk.CTkFont(size=16)).pack(side="left")
        Tooltip(btn_f.winfo_children()[-1],
                "Показать информацию о файле:\nкодек, разрешение, есть ли HDR, аудио и субтитры")

        # --- Выход ---
        out_card = self._card(tab, "Папка для сохранения результата", row=2)
        out_card.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(out_card, textvariable=self.v_output, height=38,
                     placeholder_text="По умолчанию — та же папка, что и оригинал",
                     font=ctk.CTkFont(size=12),
                     fg_color=C["bg"], border_color=C["border"]
                     ).grid(row=0, column=0, sticky="ew", padx=(0,8))
        ctk.CTkButton(out_card, text="Выбрать", width=90, height=38,
                      command=self._browse_out).grid(row=0, column=1)

        # --- Имя файла ---
        name_card = self._card(tab, "Имя выходного файла", row=3)
        name_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(name_card, text="Суффикс к имени:",
                     text_color=C["muted"], font=ctk.CTkFont(size=12)
                     ).grid(row=0, column=0, sticky="w", padx=(0,10))
        ctk.CTkEntry(name_card, textvariable=self.v_suffix, width=120, height=34,
                     fg_color=C["bg"], border_color=C["border"]
                     ).grid(row=0, column=1, sticky="w", padx=(0,20))
        ctk.CTkLabel(name_card, text="Формат файла:",
                     text_color=C["muted"], font=ctk.CTkFont(size=12)
                     ).grid(row=0, column=2, sticky="w", padx=(0,10))
        ctk.CTkSegmentedButton(name_card, values=[".mkv", ".mp4"],
                                variable=self.v_container, width=140
                                ).grid(row=0, column=3, sticky="w")

        # Подсказка
        hint = ctk.CTkFrame(tab, fg_color="transparent")
        hint.grid(row=4, column=0, sticky="ew", padx=4, pady=(4,0))
        ctk.CTkLabel(hint,
                     text="💡  Пример: фильм.mkv + суффикс «_SDR» = фильм_SDR.mkv",
                     font=ctk.CTkFont(size=11), text_color=C["muted"]
                     ).pack(anchor="w")

    # ── Вкладка: Качество ─────────────────────────────────────────────────────
    def _build_tab_quality(self):
        tab = self.tab_bodies["🎨  Качество"]
        tab.grid_columnconfigure(0, weight=1)

        summary_card = self._card(tab, "Активный видеопайплайн", row=0)
        self.pipeline_summary_lbl = ctk.CTkLabel(
            summary_card,
            text="",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=C["text"],
            anchor="w",
            justify="left",
            wraplength=700,
        )
        self.pipeline_summary_lbl.grid(row=0, column=0, sticky="w")
        self.pipeline_note_lbl = ctk.CTkLabel(
            summary_card,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=C["muted"],
            anchor="w",
            justify="left",
            wraplength=700,
        )
        self.pipeline_note_lbl.grid(row=1, column=0, sticky="w", pady=(6,0))

        # --- Кодек ---
        enc_card = self._card(tab, "Видеокарта / способ кодирования", row=1)
        enc_card.grid_columnconfigure(0, weight=1)
        ctk.CTkOptionMenu(enc_card, variable=self.v_encoder,
                           values=list(ENCODER_OPTIONS),
                           height=38, font=ctk.CTkFont(size=12),
                           fg_color=C["bg"], button_color=C["accent"],
                           dropdown_fg_color=C["card"],
                           command=self._on_encoder
                           ).grid(row=0, column=0, sticky="ew")
        hint = ctk.CTkLabel(enc_card,
                             text="Если у вас NVIDIA GTX/RTX — выбирайте первые два варианта. Кодирование уйдёт на видеокарту.",
                             font=ctk.CTkFont(size=11), text_color=C["muted"])
        hint.grid(row=1, column=0, sticky="w", pady=(6,0))

        # --- Движок HDR → SDR ---
        proc_card = self._card(tab, "Где выполнять HDR → SDR", row=2)
        proc_card.grid_columnconfigure(0, weight=1)
        ctk.CTkOptionMenu(proc_card, variable=self.v_processing,
                           values=list(PROCESSING_OPTIONS),
                           height=38, font=ctk.CTkFont(size=12),
                           fg_color=C["bg"], button_color=C["accent"],
                           dropdown_fg_color=C["card"],
                           command=self._on_processing
                           ).grid(row=0, column=0, sticky="ew")
        self.processing_hint_lbl = ctk.CTkLabel(
            proc_card,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=C["muted"],
            justify="left",
            wraplength=640,
        )
        self.processing_hint_lbl.grid(row=1, column=0, sticky="w", pady=(6,0))

        # --- Скорость ---
        spd_card = self._card(tab, "Скорость конвертации", row=3)
        spd_card.grid_columnconfigure(0, weight=1)
        ctk.CTkSegmentedButton(spd_card, values=list(SPEED_OPTIONS),
                                variable=self.v_speed,
                                font=ctk.CTkFont(size=11),
                                selected_color=C["accent"],
                                selected_hover_color=C["accent_soft"],
                                unselected_color=C["surface_alt"],
                                unselected_hover_color=C["border"],
                                ).grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(
            spd_card,
            text="Турбо = максимум FPS на NVENC. Баланс = хороший темп и качество. Качество = заметно медленнее, но чище картинка.",
            font=ctk.CTkFont(size=11),
            text_color=C["muted"],
            justify="left",
            wraplength=700,
        ).grid(row=1, column=0, sticky="w", pady=(8,0))

        # --- Качество ---
        q_card = self._card(tab, "Качество картинки", row=4)
        q_card.grid_columnconfigure(0, weight=1)

        # Шкала визуальная
        scale_f = ctk.CTkFrame(q_card, fg_color="transparent")
        scale_f.grid(row=0, column=0, sticky="ew")
        scale_f.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(scale_f, text="Лучше\n(больше файл)",
                     font=ctk.CTkFont(size=10), text_color=C["green"],
                     justify="center").grid(row=0, column=0, padx=(0,8))

        self.quality_slider = ctk.CTkSlider(scale_f, from_=12, to=28,
                                             number_of_steps=16,
                                             variable=self.v_quality,
                                             command=self._on_quality,
                                             button_color=C["accent"],
                                             progress_color=C["accent2"])
        self.quality_slider.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(scale_f, text="Меньше\n(меньше файл)",
                     font=ctk.CTkFont(size=10), text_color=C["yellow"],
                     justify="center").grid(row=0, column=2, padx=(8,0))

        self.quality_lbl = ctk.CTkLabel(q_card, text=self._quality_text(18),
                                         font=ctk.CTkFont(size=12),
                                         text_color=C["muted"])
        self.quality_lbl.grid(row=1, column=0, pady=(6,0))

        # --- Метод тон-маппинга ---
        tm_card = self._card(tab, "Метод цветокоррекции HDR → SDR", row=5)
        tm_card.grid_columnconfigure(0, weight=1)
        ctk.CTkOptionMenu(tm_card, variable=self.v_tonemap,
                           values=list(TONEMAP_OPTIONS),
                           height=36, font=ctk.CTkFont(size=12),
                           fg_color=C["bg"], button_color=C["accent"],
                           dropdown_fg_color=C["card"]
                           ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(tm_card,
                     text="Hable — золотой стандарт. Сохраняет детали в тёмных и светлых сценах.\n"
                          "Reinhard — чуть ярче. Mobius — мягче, ближе к оригинальному HDR.",
                     font=ctk.CTkFont(size=11), text_color=C["muted"], justify="left"
                     ).grid(row=1, column=0, sticky="w", pady=(6,0))

        # --- Расширенные (сворачиваемые) ---
        adv_card = self._card(tab, "Расширенные настройки цвета", row=6)
        adv_card.grid_columnconfigure(0, weight=1)

        # Яркость экрана
        br_f = ctk.CTkFrame(adv_card, fg_color="transparent")
        br_f.grid(row=0, column=0, sticky="ew", pady=(0,8))
        br_f.grid_columnconfigure(1, weight=1)
        lbl_br = ctk.CTkLabel(br_f,
                               text="Яркость проектора (нит):",
                               font=ctk.CTkFont(size=12), text_color=C["text"],
                               width=200, anchor="w")
        lbl_br.grid(row=0, column=0, sticky="w")
        Tooltip(lbl_br,
                "Яркость вашего проектора/экрана в нитах.\n\n"
                "• Проектор домашний: 100–300 нит → ставьте 100\n"
                "• ТВ обычный: 300–500 нит → ставьте 300\n"
                "• ТВ яркий/OLED: 500–1000 нит → ставьте 500\n\n"
                "Это помогает правильно пересчитать яркость при конвертации.")
        self.br_lbl = ctk.CTkLabel(br_f, text="100 нит",
                                    font=ctk.CTkFont(size=12, weight="bold"),
                                    text_color=C["accent"], width=70)
        self.br_lbl.grid(row=0, column=2, sticky="e")
        ctk.CTkSlider(br_f, from_=50, to=1000, number_of_steps=38,
                      variable=self.v_brightness,
                      command=lambda v: self.br_lbl.configure(text=f"{int(v)} нит"),
                      button_color=C["accent"], progress_color=C["accent2"]
                      ).grid(row=0, column=1, sticky="ew", padx=8)

        # Насыщенность
        sat_f = ctk.CTkFrame(adv_card, fg_color="transparent")
        sat_f.grid(row=1, column=0, sticky="ew")
        sat_f.grid_columnconfigure(1, weight=1)
        lbl_sat = ctk.CTkLabel(sat_f,
                                text="Насыщенность цвета:",
                                font=ctk.CTkFont(size=12), text_color=C["text"],
                                width=200, anchor="w")
        lbl_sat.grid(row=0, column=0, sticky="w")
        Tooltip(lbl_sat,
                "Управляет насыщенностью цветов после конвертации.\n\n"
                "• 0.0 — рекомендуется, цвета сохраняются максимально\n"
                "• 0.5 — слегка снижает насыщенность пересвеченных зон\n"
                "• 1.0 — сильно снижает насыщенность (может выглядеть бледно)\n\n"
                "Оставьте 0.0 если не знаете зачем это нужно.")
        self.sat_lbl = ctk.CTkLabel(sat_f, text="0.0",
                                     font=ctk.CTkFont(size=12, weight="bold"),
                                     text_color=C["accent"], width=70)
        self.sat_lbl.grid(row=0, column=2, sticky="e")
        ctk.CTkSlider(sat_f, from_=0, to=1, number_of_steps=20,
                      variable=self.v_saturation,
                      command=lambda v: self.sat_lbl.configure(text=f"{v:.1f}"),
                      button_color=C["accent"], progress_color=C["accent2"]
                      ).grid(row=0, column=1, sticky="ew", padx=8)

    # ── Вкладка: Аудио ────────────────────────────────────────────────────────
    def _build_tab_audio(self):
        tab = self.tab_bodies["🔊  Аудио"]
        tab.grid_columnconfigure(0, weight=1)

        audio_card = self._card(tab, "Что делать со звуком?", row=0)
        audio_card.grid_columnconfigure(0, weight=1)

        for i, (label, val) in enumerate(AUDIO_OPTIONS.items()):
            rb_f = ctk.CTkFrame(audio_card, fg_color=C["surface_alt"], corner_radius=12,
                                border_width=1, border_color=C["border"])
            rb_f.grid(row=i, column=0, sticky="ew", pady=3)
            rb_f.grid_columnconfigure(1, weight=1)
            rb = ctk.CTkRadioButton(rb_f, text="", variable=self.v_audio,
                                     value=val, width=24)
            rb.grid(row=0, column=0, padx=(10,6), pady=10)
            ctk.CTkLabel(rb_f, text=label, font=ctk.CTkFont(size=13),
                          text_color=C["text"], anchor="w"
                          ).grid(row=0, column=1, sticky="w", pady=10)

        hint_card = self._card(tab, "💡 Совет", row=1)
        ctk.CTkLabel(hint_card,
                     text="Большинство современных телевизоров и проекторов понимают Dolby TrueHD и DTS.\n"
                          "Выбирайте «Оставить как есть» — это не трогает звук и работает быстрее.\n\n"
                          "Если Plex показывает ошибку при воспроизведении звука — выберите AAC.",
                     font=ctk.CTkFont(size=12), text_color=C["muted"],
                     justify="left", wraplength=600
                     ).pack(anchor="w")

        subs_card = self._card(tab, "Субтитры", row=2)
        ctk.CTkCheckBox(subs_card,
                         text="Перенести субтитры в новый файл (рекомендуется)",
                         variable=self.v_subs,
                         font=ctk.CTkFont(size=13),
                         checkbox_width=22, checkbox_height=22
                         ).pack(anchor="w")

    # ── Вкладка: Настройки ────────────────────────────────────────────────────
    def _build_tab_settings(self):
        tab = self.tab_bodies["⚙️  Настройки"]
        tab.grid_columnconfigure(0, weight=1)

        ffmpeg_card = self._card(tab, "Путь к FFmpeg", row=0)
        ffmpeg_card.grid_columnconfigure(0, weight=1)

        for r_i, (lbl, var, cmd, dflt) in enumerate([
            ("ffmpeg.exe", self.v_ffmpeg, self._browse_ffmpeg, DEFAULT_FFMPEG),
            ("ffprobe.exe", self.v_ffprobe, self._browse_ffprobe, DEFAULT_FFPROBE),
        ]):
            row_f = ctk.CTkFrame(ffmpeg_card, fg_color="transparent")
            row_f.grid(row=r_i, column=0, sticky="ew", pady=3)
            row_f.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row_f, text=lbl, width=90, font=ctk.CTkFont(size=12),
                          text_color=C["muted"]).grid(row=0,column=0,padx=(0,8))
            ctk.CTkEntry(row_f, textvariable=var, height=34,
                          fg_color=C["bg"], border_color=C["border"],
                          font=ctk.CTkFont(size=11)
                          ).grid(row=0, column=1, sticky="ew", padx=(0,8))
            ctk.CTkButton(row_f, text="Обзор", width=80, height=34,
                           fg_color=C["surface_alt"], hover_color=C["border"],
                           command=cmd).grid(row=0, column=2)

        ctk.CTkButton(ffmpeg_card, text="✓  Проверить FFmpeg",
                       height=36, command=self._check_ffmpeg,
                       fg_color=C["surface_alt"], hover_color=C["border"],
                       font=ctk.CTkFont(size=12)
                       ).grid(row=2, column=0, sticky="w", pady=(8,0))

        # Дополнительно
        extra_card = self._card(tab, "Дополнительно", row=1)
        extra_card.grid_columnconfigure(0, weight=1)

        options = [
            (self.v_overwrite, "Перезаписывать файлы, если они уже существуют",
             "По умолчанию конвертер пропускает уже готовые файлы"),
            (self.v_shutdown,  "Выключить компьютер после завершения",
             "Удобно запустить на ночь — ПК сам выключится"),
        ]
        for i, (var, title, sub) in enumerate(options):
            f = ctk.CTkFrame(extra_card, fg_color=C["surface_alt"], corner_radius=12,
                             border_width=1, border_color=C["border"])
            f.grid(row=i, column=0, sticky="ew", pady=3)
            f.grid_columnconfigure(1, weight=1)
            ctk.CTkCheckBox(f, text="", variable=var, width=24
                             ).grid(row=0, column=0, padx=10, pady=8)
            tf = ctk.CTkFrame(f, fg_color="transparent")
            tf.grid(row=0, column=1, sticky="w", pady=8)
            ctk.CTkLabel(tf, text=title, font=ctk.CTkFont(size=13),
                          text_color=C["text"]).pack(anchor="w")
            ctk.CTkLabel(tf, text=sub, font=ctk.CTkFont(size=11),
                          text_color=C["muted"]).pack(anchor="w")

        ctk.CTkButton(extra_card, text="💾  Сохранить настройки",
                       height=36, command=self._save_all,
                       fg_color=C["accent"], hover_color=C["accent_soft"], text_color="#08312d",
                       font=ctk.CTkFont(size=12, weight="bold")
                       ).grid(row=2, column=0, sticky="w", pady=(12,0))

    # ── Нижняя панель ─────────────────────────────────────────────────────────
    def _build_bottom(self):
        bottom = ctk.CTkFrame(
            self,
            fg_color=C["surface"],
            corner_radius=22,
            border_width=1,
            border_color=C["border"],
        )
        bottom.grid(row=1, column=0, sticky="ew", padx=16, pady=(0,16))
        bottom.grid_columnconfigure(1, weight=1)

        # Прогресс
        prog_f = ctk.CTkFrame(bottom, fg_color="transparent")
        prog_f.grid(row=1, column=0, columnspan=4, sticky="ew", padx=16, pady=(14,8))
        prog_f.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(prog_f, height=6,
                                            progress_color=C["accent"],
                                            fg_color=C["border"])
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0,12))
        self.progress.set(0)
        self.status_lbl = ctk.CTkLabel(prog_f, text="Готов к работе",
                                        font=ctk.CTkFont(size=11),
                                        text_color=C["muted"], width=200, anchor="e")
        self.status_lbl.grid(row=0, column=1, sticky="e")

        # Кнопки
        btn_f = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_f.grid(row=2, column=0, columnspan=4, sticky="ew", padx=16, pady=(0,14))
        btn_f.grid_columnconfigure(1, weight=1)

        self.btn_start = ctk.CTkButton(
            btn_f, text="▶   Начать конвертацию",
            width=220, height=46,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=C["accent"], hover_color=C["accent_soft"],
            text_color="#08312d",
            command=self._start)
        self.btn_start.grid(row=0, column=0, padx=(0,10))

        self.btn_stop = ctk.CTkButton(
            btn_f, text="⏹  Остановить",
            width=140, height=46,
            font=ctk.CTkFont(size=13),
            fg_color="#542626", hover_color=C["red"],
            state="disabled", command=self._cancel)
        self.btn_stop.grid(row=0, column=1, sticky="w")

        ctk.CTkButton(
            btn_f, text="Показать команду FFmpeg",
            width=200, height=46,
            font=ctk.CTkFont(size=12),
            fg_color=C["surface_alt"], hover_color=C["border"],
            command=self._show_cmd
        ).grid(row=0, column=2, padx=(0,0), sticky="e")

        # Лог (раскрываемый)
        self.log_box = ctk.CTkTextbox(
            bottom, height=110,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=C["bg"], text_color=C["muted"],
            border_color=C["border"], border_width=1,
            state="disabled")
        self.log_box.grid(row=3, column=0, columnspan=4,
                           sticky="ew", padx=16, pady=(0,14))

    # ── Вспомогательные ───────────────────────────────────────────────────────
    def _card(self, parent, title, row):
        outer = ctk.CTkFrame(
            parent,
            fg_color=C["card"],
            corner_radius=18,
            border_width=1,
            border_color=C["border"],
        )
        outer.grid(row=row, column=0, sticky="ew", pady=(0,8))
        outer.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(outer, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12,8))
        ctk.CTkLabel(
            head,
            text="●",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=C["accent2"],
        ).pack(side="left", padx=(0,6))
        ctk.CTkLabel(
            head,
            text=title,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=C["text"],
        ).pack(side="left")
        ctk.CTkFrame(outer, height=1, fg_color=C["border"]
                     ).grid(row=1, column=0, sticky="ew", padx=14)
        inner = ctk.CTkFrame(outer, fg_color="transparent")
        inner.grid(row=2, column=0, sticky="ew", padx=14, pady=(12,14))
        inner.grid_columnconfigure(0, weight=1)
        return inner

    def _radio_card(self, parent, title, subtitle, value, col):
        f = ctk.CTkFrame(parent, fg_color=C["surface_alt"], corner_radius=14,
                         border_width=1, border_color=C["border"])
        f.grid(row=0, column=col, sticky="ew", padx=(0,8) if col==0 else (8,0), pady=0)
        f.grid_columnconfigure(0, weight=1)
        ctk.CTkRadioButton(f, text=title, variable=self.v_mode, value=value,
                            font=ctk.CTkFont(size=13, weight="bold"),
                            text_color=C["text"]
                            ).grid(row=0, column=0, sticky="w", padx=12, pady=(10,2))
        ctk.CTkLabel(f, text=subtitle, font=ctk.CTkFont(size=11),
                     text_color=C["muted"], anchor="w"
                     ).grid(row=1, column=0, sticky="w", padx=30, pady=(0,10))

    def _quality_text(self, val):
        val = int(val)
        if val <= 15: return f"Значение {val} — Максимальное качество (файл будет большим)"
        if val <= 19: return f"Значение {val} — Отличное качество ✓ рекомендуется"
        if val <= 23: return f"Значение {val} — Хорошее качество, файл меньше"
        return        f"Значение {val} — Среднее качество, маленький файл"

    def _on_quality(self, v):
        self.quality_lbl.configure(text=self._quality_text(v))

    def _refresh_pipeline_summary(self):
        requested = PROCESSING_OPTIONS.get(self.v_processing.get(), "auto")
        encoder = ENCODER_OPTIONS.get(self.v_encoder.get(), "hevc_nvenc")
        ffmpeg = self.v_ffmpeg.get().strip()
        if not ffmpeg:
            summary = "Укажите путь к FFmpeg, чтобы увидеть итоговый пайплайн."
            note = "Без FFmpeg приложение всё равно сохранит настройки, но не сможет проверить доступность GPU-ускорения."
        else:
            caps = inspect_ffmpeg(ffmpeg)
            if not caps["version"]:
                summary = "FFmpeg не найден"
                note = "Укажите корректный путь к ffmpeg.exe, чтобы приложение смогло проверить NVENC, Vulkan и OpenCL."
            else:
                backend = resolve_processing_backend(requested, caps)
                summary = f"{PIPELINE_LABELS[backend]} + {ENCODER_LABELS.get(encoder, encoder)}"
                if backend == "libplacebo":
                    summary += " + CUDA decode" if any(caps["cuvid"].values()) else ""
                note = {
                    "libplacebo": "Лучший режим для современных RTX/GTX. Использует GPU-шейдеры для HDR→SDR и обычно даёт максимальный FPS.",
                    "opencl": "Совместимый GPU-режим. Полезен, если Vulkan/libplacebo временно недоступен.",
                    "cpu": "Полностью совместимый путь через процессор. Сильно медленнее GPU, но самый надёжный.",
                }[backend]
                if requested != "auto" and backend != requested:
                    note += " Запрошенный режим недоступен в вашей сборке FFmpeg, поэтому приложение автоматически переключится на fallback."
        if hasattr(self, "pipeline_summary_lbl"):
            self.pipeline_summary_lbl.configure(text=summary)
        if hasattr(self, "pipeline_note_lbl"):
            self.pipeline_note_lbl.configure(text=note)

    def _on_processing(self, _=None):
        mode = PROCESSING_OPTIONS.get(self.v_processing.get(), "auto")
        hints = {
            "auto": "Сначала попробует GPU Vulkan/libplacebo, затем GPU OpenCL и только потом CPU. Для RTX это лучший режим по умолчанию.",
            "libplacebo": "Самый быстрый режим для современных NVIDIA: HDR→SDR на GPU, плюс CUDA-декодирование, если кодек поддерживается.",
            "opencl": "Запасной GPU-режим. Обычно заметно быстрее CPU, но чуть медленнее Vulkan/libplacebo.",
            "cpu": "Полностью совместимый путь, но HDR→SDR считается на процессоре. Используйте его, если GPU-фильтры не запускаются.",
        }
        if hasattr(self, "processing_hint_lbl"):
            self.processing_hint_lbl.configure(text=hints.get(mode, ""))
        self._refresh_pipeline_summary()

    def _on_encoder(self, _=None):
        self._refresh_pipeline_summary()

    # ── Браузеры ──────────────────────────────────────────────────────────────
    def _browse_in(self):
        if self.v_mode.get() == "single":
            p = filedialog.askopenfilename(
                filetypes=[("Видео","*.mkv *.mp4 *.m2ts *.ts *.avi *.mov *.webm"),("Все","*.*")])
        else:
            p = filedialog.askdirectory()
        if p:
            self.v_input.set(p)
            if not self.v_output.get():
                self.v_output.set(str(Path(p).parent if self.v_mode.get()=="single" else p))

    def _browse_out(self):
        p = filedialog.askdirectory()
        if p: self.v_output.set(p)

    def _browse_ffmpeg(self):
        p = filedialog.askopenfilename(filetypes=[("Exe","*.exe"),("Все","*.*")])
        if p:
            self.v_ffmpeg.set(p)
            self._refresh_pipeline_summary()

    def _browse_ffprobe(self):
        p = filedialog.askopenfilename(filetypes=[("Exe","*.exe"),("Все","*.*")])
        if p: self.v_ffprobe.set(p)

    # ── Проверка и инфо ───────────────────────────────────────────────────────
    def _check_ffmpeg(self):
        path = self.v_ffmpeg.get()
        try:
            caps = inspect_ffmpeg(path)
            if not caps["version"]:
                raise FileNotFoundError(path)
            auto_backend = resolve_processing_backend("auto", caps)
            self._log(f"✅ {caps['version']}")
            self._log(f"   GPU кодирование (NVENC):     {'✅ Доступно' if caps['nvenc_hevc'] or caps['nvenc_h264'] else '❌ Недоступно — будет CPU'}")
            self._log(f"   CUDA-декодирование AV1:      {'✅ Доступно' if caps['cuvid'].get('av1')  else '⚠️  Нет'}")
            self._log(f"   CUDA-декодирование HEVC:     {'✅ Доступно' if caps['cuvid'].get('hevc') else '⚠️  Нет'}")
            self._log(f"   GPU HDR→SDR Vulkan:          {'✅ Доступно' if caps['libplacebo'] and caps['vulkan'] else '⚠️  Нет'}")
            self._log(f"   GPU HDR→SDR OpenCL:          {'✅ Доступно' if caps['tonemap_opencl'] and caps['opencl'] else '⚠️  Нет'}")
            self._log(f"   Авто-режим сейчас выберет:   {PIPELINE_LABELS[auto_backend]}")
        except Exception as e:
            self._log(f"❌ FFmpeg не найден по пути: {path}")
            self._log(f"   Скачайте с https://www.gyan.dev/ffmpeg/builds/")

    def _probe(self):
        inp = self.v_input.get().strip()
        if not inp or not os.path.isfile(inp):
            messagebox.showinfo("Инфо","Сначала выберите файл"); return
        data = probe_info(self.v_ffprobe.get(), inp)
        if not data:
            self._log("❌ Не удалось получить информацию о файле"); return
        self._log("\n══ Информация о файле ══")
        for s in data.get("streams",[]):
            t = s.get("codec_type","")
            if t=="video":
                ct = s.get("color_transfer","")
                is_hdr = ct in ("smpte2084","arib-std-b67","smpte428")
                self._log(f"  📹 Видео: {s.get('codec_name','?').upper()}  "
                          f"{s.get('width','?')}×{s.get('height','?')}  "
                          f"{s.get('r_frame_rate','?')} fps")
                self._log(f"     HDR: {'✅ Да — конвертация нужна!' if is_hdr else '❌ Нет (файл уже SDR)'}")
            elif t=="audio":
                self._log(f"  🔊 Аудио: {s.get('codec_name','?')}  "
                          f"{s.get('channel_layout','?')}  "
                          f"{s.get('sample_rate','?')} Гц")
            elif t=="subtitle":
                lang = s.get("tags",{}).get("language","?")
                self._log(f"  💬 Субтитры: {s.get('codec_name','?')}  [{lang}]")
        fmt = data.get("format",{})
        dur  = float(fmt.get("duration",0))
        size = int(fmt.get("size",0))/(1024**3)
        self._log(f"  🕒 Длительность: {int(dur//3600)}ч {int((dur%3600)//60)}м  |  "
                  f"📦 Размер: {size:.2f} ГБ")

    # ── Логика конвертации ────────────────────────────────────────────────────
    def _get_config(self):
        spd = SPEED_OPTIONS[self.v_speed.get()]
        return {
            "encoder":   ENCODER_OPTIONS[self.v_encoder.get()],
            "processing": PROCESSING_OPTIONS[self.v_processing.get()],
            "speed_nv":  spd["nv"],
            "speed_cpu": spd["cpu"],
            "cq":        self.v_quality.get(),
            "tonemap":   TONEMAP_OPTIONS[self.v_tonemap.get()],
            "npl":       self.v_brightness.get(),
            "desat":     round(self.v_saturation.get(),1),
            "audio":     self.v_audio.get(),
            "copy_subs": self.v_subs.get(),
        }

    def _make_out(self, inp):
        p  = Path(inp)
        od = Path(self.v_output.get()) if self.v_output.get() else p.parent
        return str(od / f"{p.stem}{self.v_suffix.get()}{self.v_container.get()}")

    def _collect(self):
        inp = self.v_input.get().strip()
        if not inp:
            messagebox.showerror("Ошибка","Выберите файл или папку"); return []
        if self.v_mode.get()=="single":
            if not os.path.isfile(inp):
                messagebox.showerror("Ошибка",f"Файл не найден:\n{inp}"); return []
            return [inp]
        if not os.path.isdir(inp):
            messagebox.showerror("Ошибка",f"Папка не найдена:\n{inp}"); return []
        files = sorted(str(f) for f in Path(inp).rglob("*") if f.suffix.lower() in VIDEO_EXTS)
        if not files:
            messagebox.showerror("Ошибка","В папке нет видеофайлов"); return []
        return files

    def _start(self):
        files = self._collect()
        if not files: return
        od = self.v_output.get().strip()
        if od: os.makedirs(od, exist_ok=True)
        self._cancel_flag = False
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.after(0, lambda: self.progress.set(0))
        threading.Thread(target=self._run, args=(files,), daemon=True).start()

    def _cancel(self):
        self._cancel_flag = True
        if self._proc:
            try: self._proc.terminate()
            except: pass
        self._log("⏹ Остановлено")
        self._set_status("Остановлено")

    def _run(self, files):
        total   = len(files)
        cfg     = self._get_config()
        ffmpeg  = self.v_ffmpeg.get()
        ffprobe = self.v_ffprobe.get()

        for idx, inp in enumerate(files, 1):
            if self._cancel_flag: break
            out = self._make_out(inp)
            if not self.v_overwrite.get() and os.path.exists(out):
                self._log(f"⏭ Пропуск (уже есть): {Path(out).name}"); continue

            codec = probe_codec(ffprobe, inp)
            duration = probe_duration(ffprobe, inp)
            self._log(f"\n[{idx}/{total}] {Path(inp).name}  [{codec.upper() or '?'}]")
            self._log(f"   ➜ {Path(out).name}")
            self._set_status(f"Обрабатываю {idx} из {total}…")

            job = build_job(ffmpeg, ffprobe, inp, out, cfg)
            cmd = job["cmd"]
            pipeline = job["pipeline_label"]
            if job["cuda_decode"]:
                pipeline += " + CUDA decode"
            self._log(f"   HDR→SDR: {pipeline} + {job['encoder_label']}")
            if job["fallback"]:
                self._log("   ⚠ Запрошенный GPU-режим недоступен в вашей сборке FFmpeg — включён безопасный fallback.")
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
                fps_re   = re.compile(r"fps=\s*([\d.]+)")
                speed_re = re.compile(r"speed=\s*([\d.]+)x")
                time_re  = re.compile(r"time=(\d+):(\d+):([\d.]+)")
                err_re   = re.compile(r"\b(Error|Invalid|failed|Cannot)\b")
                start_ts = time.monotonic()

                for line in self._proc.stdout:
                    line = line.rstrip()
                    if "frame=" in line:
                        parts=[]
                        m=fps_re.search(line)
                        if m: parts.append(f"fps={m.group(1)}")
                        m=speed_re.search(line)
                        speed_val = float(m.group(1)) if m else 0.0
                        if m: parts.append(f"скорость={speed_val:.2f}x")
                        m=time_re.search(line)
                        if m:
                            h, mn, s = m.groups()
                            encoded_sec = int(h) * 3600 + int(mn) * 60 + float(s)
                            if duration > 0:
                                progress = max(0.0, min(1.0, encoded_sec / duration))
                                parts.append(f"готово={progress * 100:.0f}%")
                                if speed_val > 0:
                                    remaining = max(0.0, (duration - encoded_sec) / speed_val)
                                else:
                                    elapsed = max(0.001, time.monotonic() - start_ts)
                                    realtime_speed = encoded_sec / elapsed if encoded_sec > 0 else 0.0
                                    remaining = max(0.0, (duration - encoded_sec) / realtime_speed) if realtime_speed > 0 else 0.0
                                if remaining > 0:
                                    parts.append(f"осталось {format_clock(remaining)}")
                            else:
                                parts.append(f"обработано {int(encoded_sec // 60):02d}:{int(encoded_sec % 60):02d}")
                        if parts: self._set_status("  ".join(parts))
                    elif err_re.search(line) and "warning" not in line.lower():
                        self._log("  ⚠ "+line)

                self._proc.wait()
                rc = self._proc.returncode
                if rc==0:
                    sz = os.path.getsize(out)/(1024**3)
                    self._log(f"  ✅ Готово!  Размер файла: {sz:.2f} ГБ")
                else:
                    self._log(f"  ❌ Ошибка (код {rc}) — нажмите «Показать команду» для диагностики")
            except FileNotFoundError:
                self._log(f"  ❌ ffmpeg не найден: {ffmpeg}"); break
            except Exception as e:
                self._log(f"  ❌ {e}")

            self.after(0, self.progress.set, idx/total)

        self.after(0, self._done)

    def _done(self):
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self._set_status("Готов к работе")
        if not self._cancel_flag:
            self._log("\n🎉 Все файлы обработаны!")
            if self.v_shutdown.get():
                self._log("⚠ Выключение через 60 секунд…")
                subprocess.run(["shutdown","/s","/t","60"])

    def _show_cmd(self):
        inp = self.v_input.get() or "input.mkv"
        out = self._make_out(inp)
        cmd = build_cmd(self.v_ffmpeg.get(), self.v_ffprobe.get(), inp, out, self._get_config())
        win = ctk.CTkToplevel(self)
        win.title("FFmpeg команда")
        win.geometry("900x240")
        win.configure(fg_color=C["bg"])
        win.grab_set()
        ctk.CTkLabel(win, text="Вы можете скопировать и запустить эту команду вручную в командной строке:",
                     font=ctk.CTkFont(size=11), text_color=C["muted"]
                     ).pack(padx=14, pady=(12,4), anchor="w")
        tb = ctk.CTkTextbox(win, font=ctk.CTkFont(family="Consolas",size=11),
                             fg_color=C["surface"], wrap="word",
                             border_color=C["border"], border_width=1)
        tb.pack(fill="both",expand=True,padx=14,pady=(0,14))
        tb.insert("end"," ".join(f'"{a}"' if " " in a else a for a in cmd))
        tb.configure(state="disabled")

    # ── Лог / статус ─────────────────────────────────────────────────────────
    def _log(self, text):
        def _do():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", text+"\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(0, _do)

    def _set_status(self, text):
        self.after(0, lambda: self.status_lbl.configure(text=text))

    # ── Сохранение / загрузка ─────────────────────────────────────────────────
    def _save_all(self):
        save_settings({
            "ffmpeg":    self.v_ffmpeg.get(),
            "ffprobe":   self.v_ffprobe.get(),
            "encoder":   self.v_encoder.get(),
            "processing": self.v_processing.get(),
            "speed":     self.v_speed.get(),
            "quality":   self.v_quality.get(),
            "tonemap":   self.v_tonemap.get(),
            "brightness":self.v_brightness.get(),
            "saturation":self.v_saturation.get(),
            "audio":     self.v_audio.get(),
            "subs":      self.v_subs.get(),
            "overwrite": self.v_overwrite.get(),
            "suffix":    self.v_suffix.get(),
            "container": self.v_container.get(),
        })
        self._log("💾 Настройки сохранены")

    def _load_settings(self):
        s = self.settings
        pairs = [
            ("ffmpeg",    self.v_ffmpeg),
            ("ffprobe",   self.v_ffprobe),
            ("suffix",    self.v_suffix),
            ("container", self.v_container),
            ("audio",     self.v_audio),
        ]
        for k, v in pairs:
            if k in s: v.set(s[k])
        if "encoder" in s and s["encoder"] in ENCODER_OPTIONS:
            self.v_encoder.set(s["encoder"])
        if "processing" in s and s["processing"] in PROCESSING_OPTIONS:
            self.v_processing.set(s["processing"])
        if "speed"   in s and s["speed"]   in SPEED_OPTIONS:
            self.v_speed.set(s["speed"])
        if "tonemap" in s and s["tonemap"] in TONEMAP_OPTIONS:
            self.v_tonemap.set(s["tonemap"])
        for k,v,cast in [("quality",self.v_quality,int),
                          ("brightness",self.v_brightness,int),
                          ("saturation",self.v_saturation,float)]:
            if k in s: v.set(cast(s[k]))
        for k,v in [("subs",self.v_subs),("overwrite",self.v_overwrite)]:
            if k in s: v.set(bool(s[k]))
        # Sync labels
        self.quality_lbl.configure(text=self._quality_text(self.v_quality.get()))
        self._on_processing()
        self.br_lbl.configure(text=f"{self.v_brightness.get()} нит")
        self.sat_lbl.configure(text=f"{self.v_saturation.get():.1f}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
