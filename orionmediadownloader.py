import os
import re
import sys
import json
import time
import threading
import subprocess
import urllib.request
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog, messagebox

try:
    import gallery_dl
    from gallery_dl import job, config
    config.load()
    config.set(("extractor",), "directory", [])
    config.set(("extractor", "instagram"), "directory", [])
    config.set(("extractor", "twitter"), "directory", [])
    config.set(("extractor", "vk"), "directory", [])
    GALLERY_DL_AVAILABLE = True
except ImportError:
    GALLERY_DL_AVAILABLE = False

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
YTDLP_RELEASE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"


def download_instagram_direct(url: str, save_dir: str) -> bool:
    """Прямое скачивание фото из Instagram через публичные эндпоинты без необходимости авторизации."""
    match = re.search(r'instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)', url)
    if not match:
        return False
    shortcode = match.group(1)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.instagram.com/",
    }

    downloaded = 0

    embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
    try:
        req = urllib.request.Request(embed_url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            html = response.read().decode('utf-8', errors='ignore')
            img_urls = re.findall(r'<img[^>]+class="EmbeddedMediaImage"[^>]+src="([^">]+)"', html)
            if not img_urls:
                img_urls = re.findall(r'"display_url":"([^"]+)"', html)

            unique_urls = list(dict.fromkeys(img_urls))
            for idx, img_url in enumerate(unique_urls):
                clean_url = img_url.replace('&amp;', '&').replace('\\u0026', '&')
                img_req = urllib.request.Request(clean_url, headers=headers)
                suffix = f"_{idx+1}" if len(unique_urls) > 1 else ""
                out_path = os.path.join(save_dir, f"instagram_{shortcode}{suffix}.jpg")
                with urllib.request.urlopen(img_req, timeout=15) as img_resp, open(out_path, 'wb') as out_f:
                    out_f.write(img_resp.read())
                downloaded += 1
    except Exception:
        pass

    if downloaded == 0:
        media_url = f"https://www.instagram.com/p/{shortcode}/media/?size=l"
        try:
            req = urllib.request.Request(media_url, headers=headers)
            out_path = os.path.join(save_dir, f"instagram_{shortcode}.jpg")
            with urllib.request.urlopen(req, timeout=15) as resp, open(out_path, 'wb') as out_f:
                out_f.write(resp.read())
            if os.path.getsize(out_path) > 1024:
                downloaded += 1
            else:
                os.remove(out_path)
        except Exception:
            pass

    return downloaded > 0


class OrionDownloaderApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Orion Media Downloader | © 2026 egor_trpv")
        self.geometry("620x430")
        self.resizable(False, False)

        if getattr(sys, 'frozen', False):
            self.base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.argv[0])))
            exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        else:
            self.base_dir = os.path.dirname(os.path.abspath(__file__))
            exe_dir = self.base_dir

        icon_candidates = [
            os.path.join(self.base_dir, "orion_logo_hd.ico"),
            os.path.join(exe_dir, "orion_logo_hd.ico"),
            os.path.join(self.base_dir, "orion_logo.ico"),
            os.path.join(exe_dir, "orion_logo.ico"),
        ]
        for candidate in icon_candidates:
            if os.path.exists(candidate):
                self.after(200, lambda p=candidate: self.iconbitmap(p))
                break

        # Папка в AppData для хранения и автообновления yt-dlp.exe
        app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        self.engine_dir = os.path.join(app_data, "OrionMediaDownloader")
        os.makedirs(self.engine_dir, exist_ok=True)
        self.ytdlp_exe = os.path.join(self.engine_dir, "yt-dlp.exe")
        self.engine_ready = threading.Event()

        self.save_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        self.info_dict = None
        self.is_photo_post = False
        self.last_ui_update = 0.0

        # Поле ввода URL с отдельной кнопкой Paste
        self.frame_url = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_url.pack(pady=(28, 6))

        self.entry_url = ctk.CTkEntry(
            self.frame_url,
            width=435,
            placeholder_text="Paste media link here (Video or Photo)..."
        )
        self.entry_url.pack(side="left", padx=(0, 8))

        self.btn_paste = ctk.CTkButton(
            self.frame_url,
            text="Paste",
            width=75,
            command=self._paste_to_entry
        )
        self.btn_paste.pack(side="left")

        self._setup_entry_features()

        # Кнопка проверки
        self.btn_check = ctk.CTkButton(
            self,
            text="Fetch Info",
            width=180,
            command=self.start_fetch_info
        )
        self.btn_check.pack(pady=6)

        # Название медиа
        self.label_video_name = ctk.CTkLabel(
            self,
            text="",
            wraplength=520,
            font=ctk.CTkFont(size=12)
        )
        self.label_video_name.pack(pady=4)

        # Выбор качества
        self.label_quality = ctk.CTkLabel(
            self,
            text="Quality / Mode:",
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.label_quality.pack(pady=(6, 2))

        self.combo_quality = ctk.CTkComboBox(
            self,
            width=280,
            values=[
                "Best Available",
                "1080p",
                "720p",
                "480p",
                "Audio Only (MP3/M4A)"
            ]
        )
        self.combo_quality.set("Best Available")
        self.combo_quality.pack(pady=4)

        # Выбор папки
        self.frame_dir = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_dir.pack(pady=8)

        self.btn_choose_dir = ctk.CTkButton(
            self.frame_dir,
            text="Save Folder",
            width=120,
            command=self.choose_directory
        )
        self.btn_choose_dir.pack(side="left", padx=6)

        self.label_dir = ctk.CTkLabel(
            self.frame_dir,
            text=self.save_dir,
            width=380,
            anchor="w"
        )
        self.label_dir.pack(side="left", padx=6)

        # Кнопка скачивания
        self.btn_download = ctk.CTkButton(
            self,
            text="Download",
            width=220,
            height=36,
            fg_color="#2EA043",
            hover_color="#238636",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.start_download
        )
        self.btn_download.pack(pady=(10, 10))

        # Прогресс-бар
        self.progress_bar = ctk.CTkProgressBar(self, width=500, height=10)

        # Статус
        self.label_status = ctk.CTkLabel(self, text="Checking engine...", text_color="#F1E05A", wraplength=520)
        self.label_status.pack(pady=(4, 10))

        # Запуск фоновой проверки/обновления yt-dlp.exe
        threading.Thread(target=self._init_and_update_engine, daemon=True).start()

    def _init_and_update_engine(self):
        """Скачивает yt-dlp.exe при первом запуске или обновляет его в фоне."""
        try:
            if not os.path.exists(self.ytdlp_exe):
                self.after(0, lambda: self.btn_check.configure(state="disabled"))
                self.after(0, lambda: self.btn_download.configure(state="disabled"))
                self.after(0, lambda: self.set_status("First launch: downloading yt-dlp engine...", "#58A6FF"))
                tmp_path = self.ytdlp_exe + ".tmp"
                req = urllib.request.Request(
                    YTDLP_RELEASE_URL,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_path, "wb") as out_f:
                    out_f.write(resp.read())
                os.replace(tmp_path, self.ytdlp_exe)
                self.engine_ready.set()
                self.after(0, lambda: self.btn_check.configure(state="normal"))
                self.after(0, lambda: self.btn_download.configure(state="normal"))
                self.after(0, lambda: self.set_status("Ready (Engine installed)", "#4CAF50"))
            else:
                # Файл уже есть — сразу разрешаем работу и тихо обновляем в фоне
                self.engine_ready.set()
                self.after(0, lambda: self.set_status("Updating engine in background...", "gray"))
                subprocess.run(
                    [self.ytdlp_exe, "-U"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=CREATE_NO_WINDOW,
                    timeout=30
                )
                self.after(0, lambda: self._restore_ready_status())
        except Exception as e:
            if os.path.exists(self.ytdlp_exe):
                self.engine_ready.set()
                self.after(0, lambda: self.btn_check.configure(state="normal"))
                self.after(0, lambda: self.btn_download.configure(state="normal"))
                self.after(0, lambda: self.set_status("Ready (Offline mode)", "gray"))
            else:
                self.after(0, lambda: self.set_status("Failed to download yt-dlp engine", "#E5534B"))
                self.after(0, lambda: messagebox.showerror(
                    "Engine Error",
                    f"Could not download yt-dlp.exe on first launch. Check your internet connection.\n\n{e}"
                ))

    def _restore_ready_status(self):
        current = self.label_status.cget("text")
        if "Updating engine" in current or "Checking engine" in current:
            self.set_status("Ready", "gray")

    def _setup_entry_features(self):
        """Контекстное меню по ПКМ и поддержка горячих клавиш в любой раскладке."""
        self.context_menu = tk.Menu(self, tearoff=0, bg="#2b2b2b", fg="white", activebackground="#1f538d", activeforeground="white")
        self.context_menu.add_command(label="Paste", command=self._paste_to_entry)
        self.context_menu.add_command(label="Copy", command=self._copy_from_entry)
        self.context_menu.add_command(label="Cut", command=self._cut_from_entry)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Select All", command=lambda: self.entry_url.select_range(0, 'end'))
        self.context_menu.add_command(label="Clear", command=lambda: self.entry_url.delete(0, 'end'))

        def show_menu(e):
            try:
                self.context_menu.tk_popup(e.x_root, e.y_root)
            finally:
                self.context_menu.grab_release()

        self.entry_url.bind("<Button-3>", show_menu)
        if hasattr(self.entry_url, "_entry"):
            self.entry_url._entry.bind("<Button-3>", show_menu)

        def on_key(event):
            if event.state & 4 or event.state & 0x20000:
                if event.keycode == 86:
                    self._paste_to_entry()
                    return "break"
                elif event.keycode == 67:
                    self._copy_from_entry()
                    return "break"
                elif event.keycode == 65:
                    self.entry_url.select_range(0, 'end')
                    return "break"
                elif event.keycode == 88:
                    self._cut_from_entry()
                    return "break"

        self.entry_url.bind("<Key>", on_key)
        if hasattr(self.entry_url, "_entry"):
            self.entry_url._entry.bind("<Key>", on_key)

    def _paste_to_entry(self):
        try:
            text = self.clipboard_get().strip()
            if text:
                try:
                    self.entry_url.delete("sel.first", "sel.last")
                except Exception:
                    pass
                self.entry_url.insert("insert", text)
        except Exception:
            pass

    def _copy_from_entry(self):
        try:
            text = self.entry_url.selection_get()
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
        except Exception:
            pass

    def _cut_from_entry(self):
        try:
            text = self.entry_url.selection_get()
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
                self.entry_url.delete("sel.first", "sel.last")
        except Exception:
            pass

    def choose_directory(self):
        chosen = filedialog.askdirectory(initialdir=self.save_dir)
        if chosen:
            self.save_dir = chosen
            self.label_dir.configure(text=self.save_dir)

    def set_status(self, text: str, color: str = "white"):
        self.label_status.configure(text=text, text_color=color)

    def show_progress_bar(self):
        if not self.progress_bar.winfo_ismapped():
            self.progress_bar.pack(before=self.label_status, pady=(0, 6))
            self.progress_bar.set(0)

    def hide_progress_bar(self):
        if self.progress_bar.winfo_ismapped():
            self.progress_bar.pack_forget()

    def update_progress_ui(self, progress_float: float, status_text: str, color: str = "white"):
        self.after(0, lambda: self._apply_progress(progress_float, status_text, color))

    def _apply_progress(self, progress_float: float, status_text: str, color: str):
        if progress_float is not None:
            self.progress_bar.set(progress_float)
        self.label_status.configure(text=status_text, text_color=color)

    def start_fetch_info(self):
        url = self.entry_url.get().strip()
        if not url:
            messagebox.showwarning("Warning", "Please enter a valid media link.")
            return

        self.hide_progress_bar()
        self.btn_check.configure(state="disabled")
        self.set_status("Fetching information...", "#F1E05A")
        threading.Thread(target=self._fetch_info_thread, args=(url,), daemon=True).start()

    def _fetch_info_thread(self, url: str):
        self.engine_ready.wait(timeout=60)
        self.is_photo_post = False
        try:
            cmd = [
                self.ytdlp_exe,
                "--dump-json",
                "--no-playlist",
                "--no-warnings",
                "--no-color",
                url
            ]
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW
            )

            if proc.returncode == 0 and proc.stdout.strip():
                first_line = proc.stdout.strip().splitlines()[0]
                self.info_dict = json.loads(first_line)
                title = self.info_dict.get("title", "Untitled")
                display_title = f"{title[:75]}..." if len(title) > 75 else title
                self.after(0, lambda: self.label_video_name.configure(text=f"🎬 {display_title}"))
                self.after(0, lambda: self.combo_quality.configure(state="normal"))
                self.after(0, lambda: self.set_status("Video found. Ready to download.", "#4CAF50"))
            else:
                err_msg = proc.stderr.strip() or "Unknown error while fetching metadata"
                err_lower = err_msg.lower()
                if "no video" in err_lower or "not a video" in err_lower or ("instagram.com" in url and "/p/" in url):
                    self.is_photo_post = True
                    self.after(0, lambda: self.label_video_name.configure(
                        text="📷 Photo Post detected (Images will be saved in original quality)"
                    ))
                    self.after(0, lambda: self.combo_quality.configure(state="disabled"))
                    self.after(0, lambda: self.set_status("Photo post ready to download.", "#4CAF50"))
                else:
                    self.after(0, lambda: self.set_status("Failed to fetch media info", "#E5534B"))
                    self.after(0, lambda: messagebox.showerror("Error", f"Could not retrieve info from URL:\n\n{err_msg}"))
        except Exception as e:
            self.after(0, lambda: self.set_status("Failed to fetch media info", "#E5534B"))
            self.after(0, lambda: messagebox.showerror("Error", f"Could not retrieve info from URL:\n\n{e}"))
        finally:
            self.after(0, lambda: self.btn_check.configure(state="normal"))

    def start_download(self):
        url = self.entry_url.get().strip()
        if not url:
            messagebox.showwarning("Warning", "Please enter a valid media link.")
            return

        self.btn_download.configure(state="disabled")
        self.show_progress_bar()
        self.set_status("Starting download...", "#F1E05A")
        threading.Thread(target=self._download_thread, args=(url,), daemon=True).start()

    def _download_thread(self, url: str):
        self.engine_ready.wait(timeout=60)

        # 1. Загрузка фото
        if self.is_photo_post:
            self.update_progress_ui(0.5, "Downloading photos...", "#58A6FF")

            if "instagram.com" in url:
                success = download_instagram_direct(url, self.save_dir)
                if success:
                    self.update_progress_ui(1.0, "Instagram photos downloaded successfully!", "#4CAF50")
                    self.after(0, lambda: messagebox.showinfo("Success", f"Photos saved directly to:\n{self.save_dir}"))
                    try:
                        os.startfile(self.save_dir)
                    except Exception:
                        pass
                    self.after(0, lambda: self.btn_download.configure(state="normal"))
                    return

            if GALLERY_DL_AVAILABLE:
                before_files = set(os.listdir(self.save_dir))
                try:
                    config.set(("extractor",), "base-directory", self.save_dir)
                    config.set(("extractor",), "directory", [])
                    job.DownloadJob(url).run()
                    after_files = set(os.listdir(self.save_dir))
                    if after_files - before_files:
                        self.update_progress_ui(1.0, "Photos downloaded successfully!", "#4CAF50")
                        self.after(0, lambda: messagebox.showinfo("Success", f"Photos saved to:\n{self.save_dir}"))
                        try:
                            os.startfile(self.save_dir)
                        except Exception:
                            pass
                        self.after(0, lambda: self.btn_download.configure(state="normal"))
                        return
                except Exception:
                    pass

            self.update_progress_ui(0.0, "Photo download failed", "#E5534B")
            self.after(0, lambda: messagebox.showerror("Error", "Could not download photos from this post."))
            self.after(0, lambda: self.btn_download.configure(state="normal"))
            return

        # 2. Загрузка видео через внешний автообновляемый yt-dlp.exe
        quality_choice = self.combo_quality.get()
        if "1080p" in quality_choice:
            fmt = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        elif "720p" in quality_choice:
            fmt = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        elif "480p" in quality_choice:
            fmt = "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
        elif "Audio" in quality_choice:
            fmt = "bestaudio/best"
        else:
            fmt = "bestvideo+bestaudio/best"

        ffmpeg_local = os.path.join(self.base_dir, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_local):
            ffmpeg_local = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "ffmpeg.exe")

        outtmpl = os.path.join(self.save_dir, "%(title)s.%(ext)s")
        cmd = [
            self.ytdlp_exe,
            "-f", fmt,
            "-o", outtmpl,
            "--newline",
            "--no-colors",
            "--no-playlist",
        ]

        if "Audio" not in quality_choice:
            cmd.extend(["--merge-output-format", "mp4"])

        if os.path.exists(ffmpeg_local):
            cmd.extend(["--ffmpeg-location", ffmpeg_local])

        cmd.append(url)

        error_lines = []
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW
            )

            progress_re = re.compile(r'\[download\]\s+(\d+(?:\.\d+)?)%.*?(?:at\s+(\S+))?.*?(?:ETA\s+(\S+))?')

            for line in proc.stdout:
                clean_line = line.strip()
                if not clean_line:
                    continue

                if "ERROR:" in clean_line:
                    error_lines.append(clean_line)

                match = progress_re.search(clean_line)
                if match:
                    now = time.time()
                    if now - self.last_ui_update >= 0.15:
                        self.last_ui_update = now
                        pct_val = float(match.group(1))
                        progress = min(pct_val / 100.0, 1.0)
                        speed = match.group(2) or ""
                        eta = match.group(3) or ""

                        speed_text = f" • {speed}" if speed else ""
                        eta_text = f" • ETA: {eta}" if eta else ""
                        status_text = f"Downloading: {pct_val:.1f}%{speed_text}{eta_text}"
                        self.update_progress_ui(progress, status_text, "#F1E05A")
                elif clean_line.startswith("[Merger]") or clean_line.startswith("[ExtractAudio]") or clean_line.startswith("[Fixup"):
                    self.update_progress_ui(1.0, "Processing & merging media (ffmpeg)...", "#58A6FF")

            proc.wait()

            if proc.returncode == 0:
                self.update_progress_ui(1.0, "Download completed!", "#4CAF50")
                self.after(0, lambda: messagebox.showinfo("Success", f"File saved to:\n{self.save_dir}"))
                try:
                    os.startfile(self.save_dir)
                except Exception:
                    pass
            else:
                error_message = "\n".join(error_lines) if error_lines else f"yt-dlp exited with code {proc.returncode}"
                err_str = error_message.lower()

                if "no video" in err_str or "not a video" in err_str or ("instagram.com" in url and "/p/" in url):
                    if "instagram.com" in url and download_instagram_direct(url, self.save_dir):
                        self.update_progress_ui(1.0, "Photos downloaded successfully!", "#4CAF50")
                        self.after(0, lambda: messagebox.showinfo("Success", f"Photos saved directly to:\n{self.save_dir}"))
                        try:
                            os.startfile(self.save_dir)
                        except Exception:
                            pass
                        return

                self.update_progress_ui(0.0, "Download failed", "#E5534B")
                self.after(0, lambda: messagebox.showerror("Error", f"Failed to download media:\n\n{error_message}"))
        except Exception as e:
            self.update_progress_ui(0.0, "Download failed", "#E5534B")
            self.after(0, lambda: messagebox.showerror("Error", f"Failed to download media:\n\n{e}"))
        finally:
            self.after(0, lambda: self.btn_download.configure(state="normal"))


if __name__ == "__main__":
    app = OrionDownloaderApp()
    app.mainloop()
