import os
import sys
import json
import time
import hashlib
import re
import shutil
import threading
import asyncio
from pathlib import Path
from queue import Queue, Empty
from typing import Optional, List
import tempfile

import pdfplumber
import edge_tts
import pygame
from langdetect import detect
import spacy

from PyQt6.QtCore import QTimer, pyqtSignal, QObject, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QLabel,
    QFileDialog,
    QDialog,
    QLineEdit,
    QComboBox,
    QMessageBox,
    QFormLayout,
)
from PyQt6.QtGui import QAction, QTextCursor


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


class WorkerSignals(QObject):
    update_status = pyqtSignal(str)
    update_progress = pyqtSignal(float)
    update_phrase = pyqtSignal(str)
    update_ui = pyqtSignal()
    processing_done = pyqtSignal()
    voices_loaded = pyqtSignal(list)


class PDFTTS(QMainWindow):
    def __init__(self):
        super().__init__()
        # Set application metadata
        QApplication.setApplicationName("pdftts")
        QApplication.setOrganizationDomain("pdftts")

        self.setWindowTitle("PDF Stream TTS")
        self.setFixedSize(320, 256)

        # Initialize audio system
        pygame.mixer.init()

        # Application state
        self.playing = False
        self.current_phrase = 0
        self.current_page = 0
        self.pages_len = 0
        self.pdf_path: Optional[Path] = None
        self.processing = False
        self.processing_queue = Queue()
        self.processed_phrases = []
        self.tts_rate = TTS_RATE
        self.tts_voice = TTS_VOICE
        self.voices = []
        self.page_state = {}
        self.processing_thread = None
        self.preload_thread = None

        # Initialize directories and state
        TEMP_DIR.mkdir(exist_ok=True, parents=True)
        CONFIG_PATH.mkdir(exist_ok=True, parents=True)
        self.load_page_state()

        self.init_ui()
        self.init_workers()
        self.load_voices()
        self.setup_shortcuts()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        # Control buttons
        control_layout = QHBoxLayout()
        self.btn_open = QPushButton("📂")
        self.btn_open.clicked.connect(self.open_pdf)
        self.play_btn = QPushButton("▶️")
        self.play_btn.clicked.connect(self.toggle_play)
        self.cfg_btn = QPushButton("⚙️")
        self.cfg_btn.clicked.connect(self.config_window)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)

        control_layout.addWidget(self.btn_open)
        control_layout.addWidget(self.play_btn)
        control_layout.addWidget(self.cfg_btn)
        control_layout.addWidget(self.progress)

        # Navigation controls
        nav_layout = QHBoxLayout()
        self.prev_page_btn = QPushButton("⏮️")
        self.prev_page_btn.clicked.connect(self.prev_page)
        self.next_page_btn = QPushButton("⏭️")
        self.next_page_btn.clicked.connect(self.next_page)
        self.prev_phrase_btn = QPushButton("←")
        self.prev_phrase_btn.clicked.connect(self.prev_phrase)
        self.next_phrase_btn = QPushButton("→")
        self.next_phrase_btn.clicked.connect(self.next_phrase)

        self.page_entry = QLineEdit()
        self.go_page_btn = QPushButton("Go")
        self.go_page_btn.clicked.connect(self.go_to_page)

        nav_layout.addWidget(self.prev_page_btn)
        nav_layout.addWidget(self.next_page_btn)
        nav_layout.addWidget(self.prev_phrase_btn)
        nav_layout.addWidget(self.next_phrase_btn)
        nav_layout.addWidget(self.page_entry)
        nav_layout.addWidget(self.go_page_btn)

        # Text display
        self.text_display = QTextEdit()
        self.text_display.setReadOnly(True)
        self.text_display.setFontFamily("Helvetica")
        self.text_display.setFontPointSize(11)

        # Status + page indicator at the bottom
        status_layout = QHBoxLayout()
        self.status_bar = QLabel("Ready")
        self.status_bar.setStyleSheet(
            "background-color: #f0f0f0; border-top: 1px solid #808080;"
        )
        self.page_label = QLabel("Page 0/0")

        status_layout.addWidget(self.status_bar)
        status_layout.addStretch()
        status_layout.addWidget(self.page_label)

        # Build layout
        layout.addLayout(control_layout)
        layout.addLayout(nav_layout)
        layout.addWidget(self.text_display)
        layout.addLayout(status_layout)

        self.update_navigation_buttons()
        self.update_ui()

    def init_workers(self):
        self.signals = WorkerSignals()
        self.signals.update_status.connect(self.status_bar.setText)
        self.signals.update_progress.connect(
            lambda val: self.progress.setValue(int(val))
        )
        self.signals.update_phrase.connect(self.display_phrase)
        self.signals.update_ui.connect(lambda: self.update_navigation_buttons())

    def setup_shortcuts(self):
        self.play_action = QAction("Play/Pause", self)
        self.play_action.setShortcut("Space")
        self.play_action.triggered.connect(self.toggle_play)
        self.addAction(self.play_action)

        self.quit_action = QAction("Quit", self)
        self.quit_action.setShortcut("Ctrl+Q")
        self.quit_action.triggered.connect(self.quit)
        self.addAction(self.quit_action)


    def load_voices(self):
        def voice_loader():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                voices = loop.run_until_complete(edge_tts.voices.list_voices())
                # Filtrar apenas vozes multilíngues
                multilingual_voices = [
                    v for v in voices 
                    if "Multilingual" in v["ShortName"]
                ]
                print(multilingual_voices)
                self.signals.voices_loaded.emit(
                    sorted(multilingual_voices, key=lambda v: v["ShortName"])
                )
            except Exception as e:
                self.signals.update_status.emit(f"Erro ao carregar vozes: {str(e)}")
            finally:
                loop.close()

        threading.Thread(target=voice_loader, daemon=True).start()

    def update_voices(self, voices):
        self.voices = voices

    def open_pdf(self):

        QApplication.setApplicationName("pdftts.filechooser")
        QApplication.setOrganizationDomain("pdftts.filechooser")

        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", os.path.expanduser("~"), "PDF Files (*.pdf)"
        )

        QApplication.setApplicationName("pdftts")
        QApplication.setOrganizationDomain("pdftts")
        if not path:
            return

        self.pdf_path = Path(path)
        state = self.page_state.get(str(self.pdf_path), {})
        self.current_page = state.get("page", 0)
        self.tts_voice = state.get("tts_voice", TTS_VOICE)
        self.tts_rate = state.get("tts_rate", TTS_RATE)

        try:
            with pdfplumber.open(self.pdf_path) as pdf:
                self.pages_len = len(pdf.pages)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open PDF: {str(e)}")
            return

        self.update_ui()
        self.start_processing()

    def start_processing(self):
        self.stop_processing()
        self.processing = True
        self.processed_phrases = []
        self.current_phrase = 0
        self.show_loading_state(True)

        # Initialize new thread instances
        self.processing_thread = threading.Thread(
            target=self.process_page_streaming, daemon=True
        )
        self.preload_thread = threading.Thread(target=self.preload_phrases, daemon=True)

        self.processing_thread.start()
        self.preload_thread.start()
        self.update_navigation_buttons()

    def process_page_streaming(self):
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
                self.signals.update_progress.emit((idx + 1) / total * 100)

                if idx < PRELOAD_NEXT:
                    time.sleep(0.1)

            self.show_loading_state(False)
            self.update_navigation_buttons()

        except Exception as e:
            self.signals.update_status.emit(f"Error: {str(e)}")
        finally:
            self.processing_queue.put(None)

    def preload_page_phrases(self, page_number: int):
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
            self.signals.update_status.emit(f"Preload error: {str(e)}")

    def preload_phrases(self):
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
                self.signals.update_status.emit(f"Preload error: {str(e)}")

    def add_processed_phrase(self, phrase: str, audio_path: Path, idx: int):
        self.processed_phrases.append(
            {"text": phrase, "audio": audio_path, "index": idx}
        )
        self.processed_phrases.sort(key=lambda x: x["index"])

        # Update navigation after adding phrases
        self.signals.update_ui.emit()

        if idx == 0 and self.playing:
            self.playing = False
            self.start_playback()

    def generate_audio(self, phrase: str, lang: str, audio_path: Path):
        for attempt in range(MAX_RETRIES):
            try:
                communicate = edge_tts.Communicate(
                    phrase, self.select_voice(lang), rate=self.tts_rate
                )
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
        if not self.playing and self.processed_phrases:
            self.playing = True
            self.play_btn.setText("⏸️")
            threading.Thread(target=self.playback_loop, daemon=True).start()

    # Modify the playback_loop method to use QTimer for GUI updates
    def playback_loop(self):
        while self.playing and self.current_phrase < len(self.processed_phrases):
            phrase = self.processed_phrases[self.current_phrase]

            # Emit signal to update phrase text in main thread
            self.signals.update_phrase.emit(phrase["text"])

            # Preload next page if needed
            if (
                self.current_phrase >= len(self.processed_phrases) - 3
                and self.current_page < self.pages_len - 1
            ):
                next_page = self.current_page + 1
                threading.Thread(
                    target=self.preload_page_phrases, args=(next_page,), daemon=True
                ).start()

            try:
                pygame.mixer.music.load(phrase["audio"])
                pygame.mixer.music.play()

                # Wait for playback to finish without GUI calls
                while pygame.mixer.music.get_busy() and self.playing:
                    time.sleep(0.1)

                if self.playing:
                    self.current_phrase += 1
                    self.save_page_state()

            except Exception as e:
                self.signals.update_status.emit(f"Playback error: {str(e)}")
                break

        if self.playing:
            self.save_page_state()
            self.next_page()
        else:
            self.signals.update_ui.emit(lambda: self.update_navigation_buttons())



    def config_window(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.setFixedSize(300, 200)

        layout = QFormLayout(dialog)

        rate_edit = QLineEdit(self.tts_rate)
        voice_combo = QComboBox()

        # Create a dedicated method for updating voices
        def update_voice_combo(voices):
            voice_combo.clear()
            filtered_voices = [v["ShortName"] for v in voices if "Multilingual" in v["ShortName"]]
            voice_combo.addItems(filtered_voices)
            
            # Set current selection
            if self.tts_voice in filtered_voices:
                voice_combo.setCurrentText(self.tts_voice)
            elif filtered_voices:
                voice_combo.setCurrentIndex(0)

        # Connect signal once
        self.signals.voices_loaded.connect(update_voice_combo)
        
        # If we already have voices, update immediately
        if self.voices:
            update_voice_combo(self.voices)

        # Rest of the dialog setup
        layout.addRow("Speech Rate:", rate_edit)
        layout.addRow("Voice:", voice_combo)

        button_box = QHBoxLayout()
        ok_btn = QPushButton("Save")
        ok_btn.clicked.connect(
            lambda: self.save_config(
                rate_edit.text(), voice_combo.currentText(), dialog
            )
        )
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)

        button_box.addWidget(ok_btn)
        button_box.addWidget(cancel_btn)
        layout.addRow(button_box)

        # Clean up signal connection when dialog closes
        dialog.finished.connect(
            lambda: self.signals.voices_loaded.disconnect(update_voice_combo)
        )

        dialog.exec()

    def save_config(self, rate: str, voice: str, dialog: QDialog):
        self.tts_rate = rate
        self.tts_voice = voice
        self.signals.update_status.emit(f"Settings saved: Rate={rate}, Voice={voice}")
        dialog.accept()
        self.save_page_state()

    def update_ui(self):
        self.page_label.setText(f"Page {self.current_page + 1}/{self.pages_len}")
        self.progress.setValue(0)
        self.text_display.clear()
        self.update_navigation_buttons()

    def update_navigation_buttons(self):
        has_phrases = len(self.processed_phrases) > 0
        self.prev_page_btn.setEnabled(self.current_page > 0)
        self.next_page_btn.setEnabled(self.current_page < self.pages_len - 1)

        # Phrase navigation should only be enabled when we have processed phrases
        self.prev_phrase_btn.setEnabled(has_phrases and self.current_phrase > 0)
        self.next_phrase_btn.setEnabled(
            has_phrases and self.current_phrase < len(self.processed_phrases) - 1
        )

        # Enable page jump controls only if we have a valid PDF
        self.page_entry.setEnabled(self.pages_len > 0)
        self.go_page_btn.setEnabled(self.pages_len > 0)

    def show_loading_state(self, loading: bool):
        self.text_display.setReadOnly(loading)
        self.progress.setValue(0)
        self.status_bar.setText("Processing..." if loading else "Ready")

    def display_phrase(self, text: str):
        self.text_display.setPlainText(text)
        # self.text_display.moveCursor(self.text_display.textCursor().End)
        self.text_display.moveCursor(QTextCursor.MoveOperation.End)

    def clean_text(self, text: str) -> str:
        text = re.sub(r"\[.*?\]|\(.*?\)", " ", text)
        text = re.sub(r"-\n", " ", text)
        return re.sub(r"\n+", " ", text).strip()

    def split_phrases(self, text: str, lang: str) -> List[str]:
        try:
            if lang in LANG_TO_MODEL:
                nlp = spacy.load(LANG_TO_MODEL[lang])
                return [sent.text.strip() for sent in nlp(text).sents]
        except Exception:
            pass
        return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]

    def get_audio_path(self, phrase: str) -> Path:
        phrase_hash = hashlib.sha256(phrase.strip().encode()).hexdigest()
        return TEMP_DIR / f"{phrase_hash}.mp3"

    def select_voice(self, lang: str) -> str:
        return self.tts_voice

    # def stop_processing(self):
    #     self.processing = False
    #     if self.processing_thread and self.processing_thread.is_alive():
    #         self.processing_thread.join(timeout=1)
    #     if self.preload_thread and self.preload_thread.is_alive():
    #         self.preload_thread.join(timeout=1)
    # self.processing_queue.queue.clear()
    def stop_processing(self):
        self.processing = False
        if self.processing_thread is not None and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=1)
        if self.preload_thread is not None and self.preload_thread.is_alive():
            self.preload_thread.join(timeout=1)
        self.processing_queue.queue.clear()
        self.processing_thread = None
        self.preload_thread = None

    def toggle_play(self):
        if self.playing:
            self.playing = False
            pygame.mixer.music.stop()
            self.play_btn.setText("▶️")
        else:
            self.start_playback()
            self.play_btn.setText("⏸️")

    def prev_phrase(self):
        if self.current_phrase > 0:
            self.current_phrase -= 1
            self.stop_playback()
            # self.display_current_phrase()

    def next_phrase(self):
        if self.current_phrase < len(self.processed_phrases) - 1:
            self.current_phrase += 1
            self.stop_playback()
            # self.display_current_phrase()

    def prev_page(self):
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
            self.stop_playback()
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
            page_num = int(self.page_entry.text()) - 1
            if 0 <= page_num < self.pages_len:
                self.stop_processing()
                self.stop_playback()
                self.current_page = page_num
                self.current_phrase = 0
                self.save_page_state()
                self.update_ui()
                self.start_processing()
                self.playing = was_playing
            else:
                QMessageBox.warning(self, "Warning", "Page number out of range")
        except ValueError:
            QMessageBox.warning(self, "Warning", "Invalid page number")

    def display_current_phrase(self):
        if 0 <= self.current_phrase < len(self.processed_phrases):
            phrase = self.processed_phrases[self.current_phrase]
            self.display_phrase(phrase["text"])
        self.update_navigation_buttons()

    def save_page_state(self):
        if self.pdf_path:
            self.page_state[str(self.pdf_path)] = {
                "page": self.current_page,
                "tts_voice": self.tts_voice,
                "tts_rate": self.tts_rate,
            }
            try:
                STATE_FILE.write_text(json.dumps(self.page_state, indent=4))
            except Exception as e:
                self.signals.update_status.emit(f"Error saving state: {str(e)}")

    def load_page_state(self):
        try:
            if STATE_FILE.exists():
                self.page_state = json.loads(STATE_FILE.read_text())
                return
            self.page_state = {}
        except Exception as e:
            self.signals.update_status.emit(f"Error loading state: {str(e)}")
            self.page_state = {}

    def stop_playback(self):
        self.playing = False
        pygame.mixer.music.stop()
        self.play_btn.setText("▶️")
        self.display_current_phrase()

    def quit(self):
        self.stop_processing()
        self.playing = False
        pygame.mixer.quit()
        self.save_page_state()
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
        QApplication.quit()

    # def closeEvent(self, event):
    #     self.quit()
    #     event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PDFTTS()
    window.show()
    sys.exit(app.exec())
