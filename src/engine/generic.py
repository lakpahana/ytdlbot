#!/usr/bin/env python3
# coding: utf-8

# ytdlbot - generic.py

import logging
import os
from pathlib import Path

import yt_dlp

from config import AUDIO_FORMAT, PROXY
from utils import is_youtube
from database.model import get_format_settings, get_quality_settings
from engine.base import BaseDownloader


def match_filter(info_dict):
    if info_dict.get("is_live"):
        raise NotImplementedError("Skipping live video")
    return None  # Allow download for non-live videos


class YoutubeDownload(BaseDownloader):
    @staticmethod
    def get_format(m):
        return [
            # Try to get video at or below specified height with audio
            f"bestvideo[ext=mp4][height<={m}][vcodec!*=av01][vcodec!*=vp9]+bestaudio[ext=m4a]",
            f"bestvideo[ext=mp4][height<={m}]+bestaudio[ext=m4a]",
            f"bestvideo[height<={m}][vcodec^=avc]+bestaudio[acodec^=mp4a]",
            f"bestvideo[height<={m}]+bestaudio",
            # Try single file formats with height limit
            f"best[height<={m}][ext=mp4]",
            f"best[height<={m}][vcodec^=avc]",
            f"best[height<={m}]",
            # Fallback without height restriction
            "best[ext=mp4]",
            "best",
        ]

    def _setup_formats(self) -> list | None:
        if not is_youtube(self._url):
            return [None]

        quality, format_ = get_quality_settings(self._chat_id), get_format_settings(self._chat_id)
        # quality: high, medium, low, custom
        # format: audio, video, document
        formats = []
        defaults = [
            # Try to get best mp4 video (excluding av01/vp9) with m4a audio
            "bestvideo[ext=mp4][vcodec!*=av01][vcodec!*=vp9]+bestaudio[ext=m4a]",
            # Try any mp4 video with m4a audio
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]",
            # Try avc codec video with mp4a audio
            "bestvideo[vcodec^=avc]+bestaudio[acodec^=mp4a]",
            # Try any video + audio combination
            "bestvideo+bestaudio",
            # Try best single file format (should catch format 18 from the example)
            "best[ext=mp4]",
            "best[vcodec^=avc]", 
            # Try specific good formats that commonly exist
            "18",  # Common 360p mp4 format
            "22",  # Common 720p mp4 format
            "best",  # Ultimate fallback - any format
        ]
        audio = AUDIO_FORMAT or "m4a"
        maps = {
            "high-audio": [f"bestaudio[ext={audio}]"],
            "high-video": defaults,
            "high-document": defaults,
            "medium-audio": [f"bestaudio[ext={audio}]"],  # no mediumaudio :-(
            "medium-video": self.get_format(720),
            "medium-document": self.get_format(720),
            "low-audio": [f"bestaudio[ext={audio}]"],
            "low-video": self.get_format(480),
            "low-document": self.get_format(480),
            "custom-audio": "",
            "custom-video": "",
            "custom-document": "",
        }

        if quality == "custom":
            pass
            # TODO not supported yet
            # get format from ytdlp, send inlinekeyboard button to user so they can choose
            # another callback will be triggered to download the video
            # available_options = {
            #     "480P": "best[height<=480]",
            #     "720P": "best[height<=720]",
            #     "1080P": "best[height<=1080]",
            # }
            # markup, temp_row = [], []
            # for quality, data in available_options.items():
            #     temp_row.append(types.InlineKeyboardButton(quality, callback_data=data))
            #     if len(temp_row) == 3:  # Add a row every 3 buttons
            #         markup.append(temp_row)
            #         temp_row = []
            # # Add any remaining buttons as the last row
            # if temp_row:
            #     markup.append(temp_row)
            # self._bot_msg.edit_text("Choose the format", reply_markup=types.InlineKeyboardMarkup(markup))
            # return None

        formats.extend(maps[f"{quality}-{format_}"])
        # extend default formats if not high*
        if quality != "high":
            formats.extend(defaults)
        return formats

    def _download(self, formats) -> list:
        output = Path(self._tempdir.name, "%(title).70s.%(ext)s").as_posix()
        ydl_opts = {
            "progress_hooks": [lambda d: self.download_hook(d)],
            "outtmpl": output,
            "restrictfilenames": False,
            "quiet": True,
            "match_filter": match_filter,
            "concurrent_fragments": 16,
            "buffersize": 4194304,
            "retries": 6,
            "fragment_retries": 6,
            "skip_unavailable_fragments": True,
            "embed_metadata": True,
            "embed_thumbnail": True,
            "writethumbnail": False,
        }
        # add proxy if configured
        if PROXY:
            ydl_opts["proxy"] = PROXY
        # setup cookies for youtube only
        if is_youtube(self._url):
            # use cookies from browser firstly
            if browsers := os.getenv("BROWSERS"):
                ydl_opts["cookiesfrombrowser"] = browsers.split(",")
            if os.path.isfile("youtube-cookies.txt") and os.path.getsize("youtube-cookies.txt") > 100:
                ydl_opts["cookiefile"] = "youtube-cookies.txt"
            # try add extract_args if present
            if potoken := os.getenv("POTOKEN"):
                ydl_opts["extractor_args"] = {"youtube": ["player-client=web,default", f"po_token=web+{potoken}"]}
                # for new version? https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide
                # ydl_opts["extractor_args"] = {
                #     "youtube": [f"po_token=web.player+{potoken}", f"po_token=web.gvs+{potoken}"]
                # }

        if self._url.startswith("https://drive.google.com"):
            # Always use the `source` format for Google Drive URLs.
            formats = ["source"] + formats

        files = None
        # Filter out None values from formats and log the format list
        valid_formats = [f for f in formats if f is not None]
        logging.info("Starting download with %d format options: %s", len(valid_formats), valid_formats)
        
        for i, f in enumerate(valid_formats, 1):
            try:
                ydl_opts["format"] = f
                logging.info("Attempt %d/%d: Trying format: %s", i, len(valid_formats), f)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([self._url])
                files = list(Path(self._tempdir.name).glob("*"))
                if files:  # Only break if we actually got files
                    logging.info("✓ Successfully downloaded with format: %s (got %d files)", f, len(files))
                    break
                else:
                    logging.warning("Format %s completed but no files found", f)
            except yt_dlp.utils.DownloadError as e:
                logging.warning("✗ Format %s failed with DownloadError: %s", f, str(e))
                continue
            except Exception as e:
                logging.error("✗ Format %s failed with unexpected error: %s", f, str(e))
                continue
        
        # Try with no format specified as last resort
        if not files:
            try:
                ydl_opts.pop("format", None)  # Remove format specification
                logging.info("Final attempt: Trying download with no format specification (yt-dlp default)")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([self._url])
                files = list(Path(self._tempdir.name).glob("*"))
                if files:
                    logging.info("✓ Successfully downloaded with default format selection (got %d files)", len(files))
            except Exception as e:
                logging.error("✗ Final fallback failed: %s", str(e))

        if not files:
            logging.error("All %d format attempts failed for URL: %s", len(valid_formats) + 1, self._url)
            raise yt_dlp.utils.DownloadError("All format options failed")

        return files

    def _start(self, formats=None):
        # start download and upload, no cache hit
        # user can choose format by clicking on the button(custom config)
        default_formats = self._setup_formats()
        if formats is not None:
            # formats according to user choice
            default_formats = formats + self._setup_formats()
        self._download(default_formats)
        self._upload()
