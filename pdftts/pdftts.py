import tkinter as tk
from tkinter import filedialog
import pygame
import pdfplumber
import edge_tts
import os
import re
import time
import threading
from langdetect import detect
import spacy
import shutil
import json
import sys

lang_to_model = {
    "pt": "pt_core_news_sm",
    "en": "en_core_web_sm",
    "es": "es_core_news_sm",
    "fr": "fr_core_news_sm",
}


class PDFTTS:
    def __init__(self):
        self.root = tk.Tk(className="charlesneimog.pytts")
        self.root.title("PDF Viewer")
        self.width = int(512 * 0.5)
        self.height = int(256 * 0.5)
        self.root.geometry(f"{self.width}x{self.height}")
        self.pdf_path = ""

        # audio
        pygame.mixer.init()
        self.thread = None
        self.current = 0
        self.playing = False

        # tts
        self.tts_voice = "en-US-AvaMultilingualNeural"
        self.audio_number = 0
        self.tmpfolder = "/tmp/pytts"
        os.makedirs(self.tmpfolder, exist_ok=True)
        self.toread = []

        # pdf
        self.pages_len = 0

        # threads
        self.subtitle_thread = None
        self.stop_audio = threading.Event()

        # config file
        self.config_path = os.path.expanduser("~/.config/pytts")
        self.state_file = os.path.join(self.config_path, "state.json")
        os.makedirs(self.config_path, exist_ok=True)
        self.page_state = self.load_page_state()

        # GUI
        button_frame = tk.Frame(self.root)
        button_frame.pack(pady=10)

        self.select_button = tk.Button(button_frame, text="Open PDF", command=self.open_pdf)
        self.select_button.pack(side=tk.LEFT, padx=5)

        self.bprevphrase = tk.Button(button_frame, text="<", command=self.prevphrase)
        self.bprevphrase.pack(side=tk.LEFT, padx=5)

        self.bnextphrase = tk.Button(button_frame, text=">", command=self.nextphrase)
        self.bnextphrase.pack(side=tk.LEFT, padx=5)

        ## SubTitle
        self.subtitle_label = tk.Label(
            self.root,
            text="",
            wraplength=self.width,
            justify="center",
            font=("Helvetica", 18),
        )
        self.subtitle_label.pack(pady=10)

        self.page_number_label = tk.Label(self.root, text="")
        self.page_number_label.pack(pady=5)

        self.doc = None
        self.tk_image = None
        self.current_page = 0

        self.root.bind("<Left>", lambda event: self.prev_page())
        self.root.bind("<Right>", lambda event: self.next_page())
        self.root.bind("<space>", lambda event: self.toggle_play())
        self.root.bind("q", lambda event: self.quit())
        self.root.mainloop()

    def load_page_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_page_state(self):
        if self.pdf_path:
            self.page_state[self.pdf_path] = self.current_page
            with open(self.state_file, "w") as f:
                json.dump(self.page_state, f)

    def quit(self):
        self.stop_audio.set()
        pygame.mixer.music.stop()
        self.root.destroy()
        sys.exit(0)

    def readaudios(self):
        while len(self.toread) == 0:
            print("waiting...")
            time.sleep(0.3)

        while self.current < len(self.toread):
            if self.stop_audio.is_set():
                return

            textdict = self.toread[self.current]
            audio_path = textdict["audio"]
            subtitle_path = textdict["subtitle"]

            self.show_subtitles(subtitle_path)

            pygame.mixer.music.load(audio_path)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                if self.stop_audio.is_set():
                    pygame.mixer.music.stop()
                    return
                time.sleep(0.1)

            self.current += 1

        shutil.rmtree(self.tmpfolder)
        os.mkdir(self.tmpfolder)
        self.next_page()
        if self.current_page < self.pages_len:
            self.readaudios()
        threading.Thread(target=self.gettext, daemon=True).start()

    def show_subtitles(self, subtitle_path):
        def to_ms(timestamp):
            h, m, s = timestamp.split(":")
            s, ms = s.split(",")
            return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)

        def subtitle_worker():
            with open(subtitle_path, encoding="utf-8") as f:
                content = f.read()

            entries = re.findall(
                r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\s+(.*?)(?=\n\n|\Z)",
                content,
                re.DOTALL,
            )

            start_time = time.time()
            for start, end, text in entries:
                if self.stop_audio.is_set():
                    return

                start_ms = to_ms(start)
                end_ms = to_ms(end)
                now = int((time.time() - start_time) * 1000)
                wait_time = (start_ms - now) / 1000
                if wait_time > 0:
                    time.sleep(wait_time)
                if self.stop_audio.is_set():
                    return
                self.subtitle_label.config(text=text.strip().replace("\n", " "))
                duration = (end_ms - start_ms) / 1000
                time.sleep(duration)
                if self.stop_audio.is_set():
                    return
                self.subtitle_label.config(text="")

        if self.subtitle_thread and self.subtitle_thread.is_alive():
            self.stop_audio.set()
            self.subtitle_thread.join()

        self.stop_audio.clear()
        self.subtitle_thread = threading.Thread(target=subtitle_worker, daemon=True)
        self.subtitle_thread.start()

    def nextphrase(self):
        if self.current < len(self.toread) - 1:
            self.stop_audio.set()
            pygame.mixer.music.stop()
            self.subtitle_label.config(text="")
            self.current += 1
            self.start()

    def prevphrase(self):
        if self.current > 0:
            self.stop_audio.set()
            pygame.mixer.music.stop()
            self.subtitle_label.config(text="")
            self.current -= 2
            self.start()

    def start(self):
        self.stop_audio.clear()
        threading.Thread(target=self.readaudios, daemon=True).start()
        self.playing = True

    def toggle_play(self):
        if not self.playing:
            self.start()
        else:
            self.stop_audio.set()
            pygame.mixer.music.stop()
            self.subtitle_label.config(text="")
            self.playing = False

    def gettext(self):
        if self.pdf_path == "":
            return

        text = ""
        with pdfplumber.open(self.pdf_path) as pdf:
            if 0 <= self.current_page < len(pdf.pages):
                text = pdf.pages[self.current_page].extract_text()

        if not text:
            return

        try:
            lang_code = detect(text)
            if lang_code in lang_to_model:
                nlp = spacy.load(lang_to_model[lang_code])
                doc = nlp(text)
                phrases = [sent.text.strip() for sent in doc.sents]
            else:
                print(f"Idioma {lang_code} não suportado.")
                return

            self.toread.clear()
            self.audio_number = 0
            self.subtitle_label.config(text="Loading...")
            self.root.update_idletasks()

            for phrase in phrases:
                phrase = re.sub(r"\[.*?\]|\(.*?\)", "", phrase)
                phrase = re.sub(r"-\n", "", phrase).replace("\n", "")

                self.audio_number += 1
                audio_path = f"{self.tmpfolder}/{self.audio_number}.mp3"
                subtitle_path = f"{self.tmpfolder}/{self.audio_number}.srt"

                communicate = edge_tts.Communicate(phrase, self.tts_voice, rate="+15%")
                submaker = edge_tts.SubMaker()

                with open(audio_path, "wb") as audio_file:
                    for chunk in communicate.stream_sync():
                        if chunk["type"] == "audio":
                            audio_file.write(chunk["data"])
                        elif chunk["type"] == "WordBoundary":
                            submaker.feed(chunk)

                with open(subtitle_path, "w", encoding="utf-8") as srt_file:
                    srt_file.write(submaker.get_srt())

                self.toread.append(
                    {
                        "audio": audio_path,
                        "subtitle": subtitle_path,
                        "readed": False,
                        "text": phrase,
                    }
                )
                if self.audio_number == 1:
                    self.current = 0
                    # self.start()

                print(f"Processado {self.audio_number}/{len(phrases)}")

        except Exception as e:
            print(f"Erro: {e}")

    def next_page(self):
        if self.current_page < self.pages_len - 1:
            self.current_page += 1
            self.save_page_state()
            self.page_number_label.config(text=f"Page {self.current_page + 1} of {self.pages_len}")

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.save_page_state()
            self.page_number_label.config(text=f"Page {self.current_page + 1} of {self.pages_len}")

    def open_pdf(self):
        self.pdf_path = filedialog.askopenfilename(title="Select PDF file", filetypes=[("PDF files", "*.pdf")])

        with pdfplumber.open(self.pdf_path) as pdf:
            self.pages_len = len(pdf.pages)

        self.current_page = self.page_state.get(self.pdf_path, 0)

        if self.pdf_path:
            self.save_page_state()
            self.page_number_label.config(text=f"Page {self.current_page + 1} of {self.pages_len}")
            threading.Thread(target=self.gettext, daemon=True).start()


def main():
    PDFTTS()
