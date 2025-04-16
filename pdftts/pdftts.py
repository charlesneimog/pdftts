import os
import tkinter as tk
from tkinter import ttk, filedialog
from queue import Queue, Empty
import pygame
import pdfplumber
import edge_tts
import re
import hashlib
import time
import threading
from langdetect import detect
import spacy
import shutil
import json
import sys
import tempfile
from pathlib import Path
from typing import Optional, Dict, List, Union
import asyncio

# Configuration constants
LANG_TO_MODEL = {
    "pt": "pt_core_news_sm",
    "en": "en_core_web_sm",
    "es": "es_core_news_sm",
    "fr": "fr_core_news_sm",
}
TEMP_DIR = Path(tempfile.gettempdir()) / "pdftts"
CONFIG_PATH = Path.home() / ".config" / "pdftts"
STATE_FILE = CONFIG_PATH / "state.json"
MAX_RETRIES = 3
PRELOAD_NEXT = 2

TTS_RATE = "+35%"
TTS_VOICE = "en-US-AvaMultilingualNeural"


class PDFTTS:
    def __init__(self, usetkui=True):
        self.root = tk.Tk(className="charlesneimog.pdftts")
        self.root.title("PDF Stream TTS")
        self.root.geometry("300x256")

        # Initialize audio system
        pygame.mixer.init()

        # Application state
        self.playing = False
        self.current_phrase = 0
        self.current_page = 0
        self.pages_len = 0
        self.pdf_path: Optional[Path] = None

        # Processing system
        self.processing_queue = Queue()
        self.processed_phrases = []
        self.processing = False
        self.processing_thread: Optional[threading.Thread] = None
        self.preload_thread: Optional[threading.Thread] = None

        # voices
        asyncio.run(self.get_voices())  # ou agende com `create_task` se estiver em loop
        self.tts_rate = TTS_RATE
        self.tts_voice = TTS_VOICE

        # Initialize directories and state
        TEMP_DIR.mkdir(exist_ok=True, parents=True)
        CONFIG_PATH.mkdir(exist_ok=True, parents=True)
        self.page_state = self.load_page_state()

        # UI setup
        if usetkui:
            self.setup_ui()
            self.bind_events()


        self.root.mainloop()


    def setup_ui(self):
        """Initialize all UI components"""
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Control buttons (linha única)
        control_frame = tk.Frame(main_frame)

        control_frame.pack(fill=tk.X, pady=3)

        # Open PDF
        self.btn_open = tk.Button(
            control_frame, text="🗂️", width=3, command=self.open_pdf
        )
        self.btn_open.pack(side=tk.LEFT, padx=2)

        # Play Pause
        self.play_btn = tk.Button(
            control_frame, text="▶️", width=3, command=self.toggle_play
        )
        self.play_btn.pack(side=tk.LEFT, padx=2)

        # Config Button
        self.cfg_btn = tk.Button(
            control_frame, text="⚙️", width=3, command=self.config_window
        )
        self.cfg_btn.pack(side=tk.LEFT, padx=2)

        # Progress (not very useful) TODO: Make it usefull
        self.progress = ttk.Progressbar(control_frame, mode="determinate")
        self.progress.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

        # Navigation controls (reduzido e em uma só linha)
        nav_frame = tk.Frame(main_frame)
        nav_frame.pack(fill=tk.X, pady=3)

        self.prev_page_btn = tk.Button(
            nav_frame, text="⏮️", width=3, command=self.prev_page
        )
        self.prev_page_btn.pack(side=tk.LEFT, padx=1)

        self.next_page_btn = tk.Button(
            nav_frame, text="⏭️", width=3, command=self.next_page
        )
        self.next_page_btn.pack(side=tk.LEFT, padx=1)

        self.prev_phrase_btn = tk.Button(
            nav_frame, text="←", width=3, command=self.prev_phrase
        )
        self.prev_phrase_btn.pack(side=tk.LEFT, padx=1)

        self.next_phrase_btn = tk.Button(
            nav_frame, text="→", width=3, command=self.next_phrase
        )
        self.next_phrase_btn.pack(side=tk.LEFT, padx=1)

        # Page entry and Go button
        self.page_entry = tk.Entry(nav_frame, width=4)
        self.page_entry.pack(side=tk.LEFT, padx=2)

        self.go_page_btn = tk.Button(
            nav_frame, text="Ir", width=3, command=self.go_to_page
        )
        self.go_page_btn.pack(side=tk.LEFT, padx=2)


        self.page_label = tk.Label(nav_frame, text="Pg 0/0")
        self.page_label.pack(side=tk.RIGHT, padx=3)

        # Text display
        self.text_display = tk.Text(
            main_frame, wrap=tk.WORD, height=8, font=("Helvetica", 11), padx=3, pady=3
        )
        self.text_display.pack(fill=tk.BOTH, expand=True)
        self.text_display.tag_configure("highlight", background="yellow")

        # Status bar
        self.status_bar = tk.Label(
            self.root, text="Ready", bd=1, relief=tk.SUNKEN, anchor=tk.W
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        try:
            self.root.tk.call('tk_getOpenFile', '-foobarbaz')
        except:
            pass
        self.root.tk.call('set', '::tk::dialog::file::showHiddenBtn', '1')
        self.root.tk.call('set', '::tk::dialog::file::showHiddenVar', '0')

        self.update_navigation_buttons()
        self.update_ui()

    def bind_events(self):
        """Set up keyboard bindings"""
        self.root.bind("<Left>", lambda e: self.prev_page())
        self.root.bind("<Right>", lambda e: self.next_page())
        self.root.bind("<space>", lambda e: self.toggle_play())
        self.root.bind("q", lambda e: self.quit())
        self.root.bind("<Escape>", lambda e: self.quit())

    def add_processed_phrase(self, phrase: str, audio_path: Path, idx: int):
        """Add processed phrase to the list in a thread-safe manner"""
        # Append and maintain sorted order by phrase index
        self.processed_phrases.append(
            {"text": phrase, "audio": audio_path, "index": idx}
        )

        # Keep phrases sorted by their original index
        self.processed_phrases.sort(key=lambda x: x["index"])

        # Update UI with first phrase immediately
        if idx == 0:
            self.root.after(0, lambda: self.display_phrase(phrase))

    # Core functionality methods
    def open_pdf(self):
        """Handle PDF file selection"""
        home = os.path.expanduser("~")
        path = filedialog.askopenfilename(filetypes=[("PDF Files", "*.pdf")], initialdir=home)
        if not path:
            return
        self.pdf_path = Path(path)
        state = self.page_state.get(path, {})
        self.current_page = state.get("page", 0)

        # self.save_page_state()
        self.pdf_path = Path(path)
        with pdfplumber.open(self.pdf_path) as pdf:
            self.pages_len = len(pdf.pages)


        self.update_ui()
        self.start_processing()

    def start_processing(self):
        """Start processing the current page"""
        self.stop_processing()
        self.processing = True
        self.processed_phrases = []
        self.current_phrase = 0
        self.show_loading_state(True)

        self.processing_thread = threading.Thread(
            target=self.process_page_streaming, daemon=True
        )
        self.processing_thread.start()

        self.preload_thread = threading.Thread(target=self.preload_phrases, daemon=True)
        self.preload_thread.start()
        self.update_navigation_buttons()

    def process_page_streaming(self):
        """Process current page content"""
        try:
            if self.pdf_path is None:
                raise Exception("PDF not loaded")

            with pdfplumber.open(self.pdf_path) as pdf:
                page = pdf.pages[self.current_page]
                text = page.extract_text() or ""

            text = self.clean_text(text)
            if not text:
                return

            lang = detect(text)
            phrases = self.split_phrases(text, lang)
            total = len(phrases)

            for idx, phrase in enumerate(phrases):
                if not self.processing:
                    break

                self.processing_queue.put((idx, phrase, lang))
                self.update_progress((idx + 1) / total * 100)

                if idx < PRELOAD_NEXT:
                    time.sleep(0.1)

            self.show_loading_state(False)
            self.update_navigation_buttons()

        except Exception as e:
            self.update_status(f"Error: {str(e)}")
        finally:
            self.processing_queue.put(None)

    def preload_phrases(self):
        """Pre-process phrases from the queue"""
        while self.processing:
            try:
                item = self.processing_queue.get(timeout=0.1)
                if item is None:
                    break

                idx, phrase, lang = item
                audio_path = self.get_audio_path(phrase)

                if not audio_path.exists():
                    self.generate_audio(phrase, lang, audio_path)

                self.add_processed_phrase(phrase, audio_path, idx)

                if idx == 0 and self.playing:
                    self.playing = False
                    self.start_playback()

            except Empty:
                continue
            except Exception as e:
                self.update_status(f"Preload error: {str(e)}")

    def display_phrase(self, text: str):
        """Update the text display with the current phrase"""
        self.text_display.delete(1.0, tk.END)
        self.text_display.insert(tk.END, text)
        self.text_display.see(tk.END)  # Auto-scroll to show the phrase

    # config 
    async def get_voices(self):
        """Obtém e armazena as vozes disponíveis."""
        voices = await edge_tts.voices.list_voices()
        self.voices = sorted(voices, key=lambda v: v["ShortName"])


    def config_window(self):
        """Abre a janela de configurações"""
        config_win = tk.Toplevel(self.root)
        config_win.title("Configurações")
        config_win.geometry("300x250")
        config_win.transient(self.root)
        config_win.grab_set()

        # Velocidade da fala
        tk.Label(config_win, text="Velocidade da Fala (TTS Rate):").pack(pady=5)
        rate_var = tk.StringVar(value=self.tts_rate)
        rate_entry = tk.Entry(config_win, textvariable=rate_var)
        rate_entry.pack()

        # Seleção de voz
        tk.Label(config_win, text="Voz (TTS Voice):").pack(pady=5)
        voice_names = [v["ShortName"] for v in getattr(self, 'voices', [])]
        voice_var = tk.StringVar(value=self.tts_voice if 'TTS_VOICE' in globals() else voice_names[0] if voice_names else "")
        voice_menu = tk.OptionMenu(config_win, voice_var, *voice_names)
        voice_menu.pack()

        def save_config():
            self.tts_rate = rate_var.get()
            self.tts_voice = voice_var.get()
            self.update_status(f"Configurações salvas: Rate={self.tts_rate}, Voice={self.tts_voice}")
            config_win.destroy()

        tk.Button(config_win, text="Salvar", command=save_config).pack(pady=10)
        tk.Button(config_win, text="Cancelar", command=config_win.destroy).pack()


    # Navigation methods
    def prev_phrase(self):
        """Navigate to previous phrase"""
        if self.current_phrase > 0:
            self.current_phrase -= 1
            self.stop_playback()
            self.display_current_phrase()

    def next_phrase(self):
        """Navigate to next phrase"""
        if self.current_phrase < len(self.processed_phrases) - 1:
            self.current_phrase += 1
            self.stop_playback()
            self.display_current_phrase()

    def prev_page(self):
        """Navigate to previous page"""
        was_playing = self.playing
        if self.current_page > 0:
            self.stop_processing()
            self.current_page -= 1
            self.current_phrase = 0
            self.save_page_state()
            self.show_loading_state(True)
            self.update_ui()
            self.start_processing()
            self.playing = was_playing

    def next_page(self):
        was_playing = self.playing
        if self.current_page < self.pages_len - 1:
            self.stop_processing()
            self.stop_playback()  # Ensure playback is stopped and flag reset
            self.current_page += 1
            self.current_phrase = 0
            self.save_page_state()
            self.show_loading_state(True)
            self.update_ui()
            self.start_processing()
            self.playing = was_playing

    def go_to_page(self):
        was_playing = self.playing
        try:
            page_num = int(self.page_entry.get()) - 1  
            if 0 <= page_num < self.pages_len:
                self.stop_processing()
                self.stop_playback()  # Ensure playback is stopped and flag reset
                self.current_page = page_num
                self.current_phrase = 0
                self.save_page_state()
                self.update_ui()
                self.start_processing()
                self.playing = was_playing
            else:
                print("Número de página fora do intervalo.")
        except ValueError:
            print("Entrada inválida para número de página.")


    # Audio processing methods
    def generate_audio(self, phrase: str, lang: str, audio_path: Path):
        """Generate audio file for phrase"""
        voice = self.select_voice(lang)

        for attempt in range(MAX_RETRIES):
            try:
                communicate = edge_tts.Communicate(phrase, voice, rate=self.tts_rate)
                audio_data = b"".join(
                    [
                        chunk["data"]
                        for chunk in communicate.stream_sync()
                        if chunk["type"] == "audio"
                    ]
                )
                audio_path.write_bytes(audio_data)
                return
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(1)

    def start_playback(self):
        """Start audio playback"""
        if not self.playing and self.processed_phrases:
            self.playing = True
            self.play_btn.config(text="⏸️")
            threading.Thread(target=self.playback_loop, daemon=True).start()

    def playback_loop(self):
        """Main playback loop"""
        while self.playing and self.current_phrase < len(self.processed_phrases):
            phrase = self.processed_phrases[self.current_phrase]
            self.display_phrase(phrase["text"])

            # Check if we're in the last 2 phrases and there's a next page
            if (self.current_phrase >= len(self.processed_phrases) - 3) and (
                self.current_page < self.pages_len - 1
            ):
                next_page = self.current_page + 1
                threading.Thread(
                    target=self.preload_page_phrases, args=(next_page,), daemon=True
                ).start()

            try:
                pygame.mixer.music.load(phrase["audio"])
                pygame.mixer.music.play()
                self.display_phrase(phrase["text"])

                while pygame.mixer.music.get_busy() and self.playing:
                    time.sleep(0.1)

                if self.playing:
                    self.current_phrase += 1
                    self.save_page_state()

            except Exception as e:
                self.update_status(f"Playback error: {str(e)}")
                break

        if self.playing:
            self.save_page_state()
            self.next_page()

    def preload_page_phrases(self, page_number):
        """Pre-process phrases for a specific page"""
        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                page = pdf.pages[page_number]
                text = page.extract_text() or ""

            text = self.clean_text(text)
            if not text:
                return

            lang = detect(text)
            phrases = self.split_phrases(text, lang)

            for idx, phrase in enumerate(phrases):
                audio_path = self.get_audio_path(phrase)
                if not audio_path.exists():
                    self.generate_audio(phrase, lang, audio_path)

        except Exception as e:
            self.update_status(f"Preload error: {str(e)}")

    # Helper methods
    def update_progress(self, value: float):
        """Update progress bar"""
        self.root.after(0, lambda: self.progress.config(value=value))

    def update_status(self, message: str):
        """Update status bar"""
        self.root.after(0, lambda: self.status_bar.config(text=message))

    def show_loading_state(self, loading: bool):
        """Toggle loading state UI"""
        self.text_display.config(state=tk.DISABLED if loading else tk.NORMAL)
        self.progress["value"] = 0
        self.status_bar.config(text="Processing..." if loading else "Ready")

    def update_navigation_buttons(self):
        """Update button states based on current position"""
        self.prev_page_btn.config(
            state=tk.NORMAL if self.current_page > 0 else tk.DISABLED
        )
        self.next_page_btn.config(
            state=tk.NORMAL if self.current_page < self.pages_len - 1 else tk.DISABLED
        )

        # Phrases
        self.prev_phrase_btn.config(
            state=tk.NORMAL if self.current_page > 0 else tk.DISABLED
        )
        self.next_phrase_btn.config(
            state=tk.NORMAL if self.current_page < self.pages_len - 1 else tk.DISABLED
        )

    def clean_text(self, text: str) -> str:
        """Clean and normalize text"""
        text = re.sub(r"\[.*?\]|\(.*?\)", " ", text)
        text = re.sub(r"-\n", " ", text)
        return re.sub(r"\n+", " ", text).strip()

    def split_phrases(self, text: str, lang: str) -> List[str]:
        """Split text into phrases"""
        try:
            if lang in LANG_TO_MODEL:
                nlp = spacy.load(LANG_TO_MODEL[lang])
                return [sent.text.strip() for sent in nlp(text).sents]
        except Exception:
            pass
        return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]

    def get_audio_path(self, phrase: str) -> Path:
        """Generate audio file path"""
        phrase_hash = hashlib.sha256(phrase.strip().encode()).hexdigest()
        return TEMP_DIR / f"{phrase_hash}.mp3"

    def select_voice(self, lang: str) -> str:
        """Select appropriate TTS voice"""
        return self.tts_voice

    def stop_processing(self):
        """Stop background processing"""
        self.processing = False
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=1)
        if self.preload_thread and self.preload_thread.is_alive():
            self.preload_thread.join(timeout=1)
        self.processing_queue.queue.clear()

    def toggle_play(self):
        """Toggle play/pause state"""
        if self.playing:
            self.playing = False
            pygame.mixer.music.stop()
            self.play_btn.config(text="▶️")
        else:
            self.start_playback()
            self.play_btn.config(text="⏸️")

    def quit(self):
        """Clean up and exit application"""
        self.stop_processing()
        self.playing = False
        pygame.mixer.quit()
        self.save_page_state()
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
        self.root.destroy()
        sys.exit(0)



    def stop_playback(self):
        """Stop audio playback and reset state"""
        self.playing = False
        pygame.mixer.music.stop()
        self.play_btn.config(text="⏸️")

    def display_current_phrase(self):
        """Mostra a frase atual e atualiza os botões"""
        if 0 <= self.current_phrase < len(self.processed_phrases):
            phrase = self.processed_phrases[self.current_phrase]
            self.display_phrase(phrase["text"])
        self.update_navigation_buttons()


    def save_page_state(self):
        """Save current page position and TTS config"""
        if self.pdf_path:
            self.page_state[str(self.pdf_path)] = {
                "page": self.current_page,
                "tts_voice": self.tts_voice,
                "tts_rate": self.tts_rate
            }
            STATE_FILE.write_text(json.dumps(self.page_state, indent=4))


    def load_page_state(self) -> Dict[str, Dict[str, Union[int, str]]]:
        """Load saved page positions and TTS config"""
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text())
                pdf_data = data.get(str(self.pdf_path), {})
                self.current_page = pdf_data.get("page", 0)
                self.tts_voice = pdf_data.get("tts_voice", self.tts_voice)
                self.tts_rate = pdf_data.get("tts_rate", self.tts_rate)
                return data
            else:
                return {}
        except Exception:
            return {}


    def update_ui(self):
        """Update all UI elements"""
        self.page_label.config(text=f"Page {self.current_page + 1}/{self.pages_len}")
        self.progress["value"] = 0
        self.text_display.delete(1.0, tk.END)
        self.update_navigation_buttons()


def main():
    PDFTTS()


if __name__ == "__main__":
    PDFTTS()
