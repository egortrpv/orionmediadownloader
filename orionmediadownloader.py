import os
import re
import sys
import time
import threading
import urllib.request
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog, messagebox
import yt_dlp
import traceback

try:
    from yt_dlp.networking.impersonate import ImpersonateTarget
except ImportError:
    ImpersonateTarget = None

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


class YdlLogger:
    """Перехватывает внутренние ошибки и предупреждения yt-dlp."""
    def __init__(self):
        self.errors = []
        self.warnings = []

    def debug(self, msg):
        pass

    def warning(self, msg):
        clean = re.sub(r'\x1b\[[0-9;]*m', '', str(msg)).strip()
        if clean:
            self.warnings.append(clean)

    def error(self, msg):
        clean = re.sub(r'\x1b\[[0-9;]*m', '', str(msg)).strip()
        if clean:
            self.errors.append(clean)

    def get_error_text(self, exc: Exception) -> str:
        if self.errors:
            return "\n".join(self.errors)
        exc_str = re.sub(r'\x1b\[[0-9;]*m', '', str(exc)).strip()
        if exc_str:
            return f"{type(exc).__name__}: {exc_str}"
        if self.warnings:
            return "\n".join(self.warnings[-3:])
        return f"{type(exc).__name__} (empty message)"


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
        self.label_status = ctk.CTkLabel(self, text="Ready", text_color="gray", wraplength=520)
        self.label_status.pack(pady=(4, 10))

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

    def _get_base_ydl_opts(self, logger: YdlLogger, url: str = ""):
        opts = {
            "quiet": True,
            "no_warnings": False,
            "no_color": True,
            "logger": logger,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
            }
        }
        # В актуальном yt-dlp требуется объект ImpersonateTarget.from_str("chrome"), а не строка
        if "youtube.com" not in url and "youtu.be" not in url and ImpersonateTarget is not None:
            try:
                import curl_cffi
                opts["impersonate"] = ImpersonateTarget.from_str("chrome")
            except Exception:
                pass
        return opts

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
        self.is_photo_post = False
        logger = YdlLogger()
        try:
            ydl_opts = self._get_base_ydl_opts(logger, url)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self.info_dict = ydl.extract_info(url, download=False)
                title = self.info_dict.get("title", "Untitled")
                display_title = f"{title[:75]}..." if len(title) > 75 else title
                self.label_video_name.configure(text=f"🎬 {display_title}")
                self.combo_quality.configure(state="normal")
                self.set_status("Video found. Ready to download.", "#4CAF50")
        except Exception as e:
            traceback.print_exc()
            error_message = logger.get_error_text(e)
            err_msg = error_message.lower()

            if "no video" in err_msg or "not a video" in err_msg or ("instagram.com" in url and "/p/" in url):
                self.is_photo_post = True
                self.label_video_name.configure(text="📷 Photo Post detected (Images will be saved in original quality)")
                self.combo_quality.configure(state="disabled")
                self.set_status("Photo post ready to download.", "#4CAF50")
            else:
                self.set_status("Failed to fetch media info", "#E5534B")
                messagebox.showerror("Error", f"Could not retrieve info from URL:\n\n{error_message}")
        finally:
            self.btn_check.configure(state="normal")

    def start_download(self):
        url = self.entry_url.get().strip()
        if not url:
            messagebox.showwarning("Warning", "Please enter a valid media link.")
            return

        self.btn_download.configure(state="disabled")
        self.show_progress_bar()
        self.set_status("Starting download...", "#F1E05A")
        threading.Thread(target=self._download_thread, args=(url,), daemon=True).start()

    def _progress_hook(self, d):
        if d['status'] == 'downloading':
            now = time.time()
            if now - self.last_ui_update < 0.15:
                return
            self.last_ui_update = now

            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes') or 0
            progress = (downloaded / total) if total > 0 else 0.0

            raw_percent = re.sub(r'\x1b\[[0-9;]*m', '', d.get('_percent_str', '')).strip()
            raw_eta = re.sub(r'\x1b\[[0-9;]*m', '', d.get('_eta_str', '')).strip()
            raw_speed = re.sub(r'\x1b\[[0-9;]*m', '', d.get('_speed_str', '')).strip()

            eta_text = f" • ETA: {raw_eta}" if raw_eta else ""
            speed_text = f" • {raw_speed}" if raw_speed else ""
            status_text = f"Downloading: {raw_percent}{speed_text}{eta_text}"

            self.update_progress_ui(progress, status_text, "#F1E05A")
        elif d['status'] == 'finished':
            self.update_progress_ui(1.0, "Processing & merging media (ffmpeg)...", "#58A6FF")

    def _download_thread(self, url: str):
        # 1. Загрузка фото
        if self.is_photo_post:
            self.update_progress_ui(0.5, "Downloading photos...", "#58A6FF")

            if "instagram.com" in url:
                success = download_instagram_direct(url, self.save_dir)
                if success:
                    self.update_progress_ui(1.0, "Instagram photos downloaded successfully!", "#4CAF50")
                    messagebox.showinfo("Success", f"Photos saved directly to:\n{self.save_dir}")
                    try:
                        os.startfile(self.save_dir)
                    except Exception:
                        pass
                    self.btn_download.configure(state="normal")
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
                        messagebox.showinfo("Success", f"Photos saved to:\n{self.save_dir}")
                        try:
                            os.startfile(self.save_dir)
                        except Exception:
                            pass
                        self.btn_download.configure(state="normal")
                        return
                except Exception:
                    pass

            self.update_progress_ui(0.0, "Photo download failed", "#E5534B")
            messagebox.showerror("Error", "Could not download photos from this post.")
            self.btn_download.configure(state="normal")
            return

        # 2. Загрузка видео (yt-dlp)
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

        logger = YdlLogger()
        ydl_opts = self._get_base_ydl_opts(logger, url)
        ydl_opts.update({
            "format": fmt,
            "outtmpl": os.path.join(self.save_dir, "%(title)s.%(ext)s"),
            "merge_output_format": "mp4" if "Audio" not in quality_choice else None,
            "progress_hooks": [self._progress_hook],
        })

        if os.path.exists(ffmpeg_local):
            ydl_opts["ffmpeg_location"] = ffmpeg_local

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            self.update_progress_ui(1.0, "Download completed!", "#4CAF50")
            messagebox.showinfo("Success", f"File saved to:\n{self.save_dir}")
            try:
                os.startfile(self.save_dir)
            except Exception:
                pass
        except Exception as e:
            traceback.print_exc()
            error_message = logger.get_error_text(e)
            err_str = error_message.lower()

            if "no video" in err_str or "not a video" in err_str or ("instagram.com" in url and "/p/" in url):
                if "instagram.com" in url and download_instagram_direct(url, self.save_dir):
                    self.update_progress_ui(1.0, "Photos downloaded successfully!", "#4CAF50")
                    messagebox.showinfo("Success", f"Photos saved directly to:\n{self.save_dir}")
                    try:
                        os.startfile(self.save_dir)
                    except Exception:
                        pass
                    return

            self.update_progress_ui(0.0, "Download failed", "#E5534B")
            messagebox.showerror("Error", f"Failed to download media:\n\n{error_message}")
        finally:
            self.btn_download.configure(state="normal")


if __name__ == "__main__":
    app = OrionDownloaderApp()
    app.mainloop()
