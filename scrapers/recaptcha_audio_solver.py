"""
reCAPTCHA v2 Audio Solver — Self-Hosted, Zero-Cost
====================================================
Based on: https://github.com/sarperavci/GoogleRecaptchaBypass
Ported from DrissionPage to Playwright for our scraper stack.

Method:
  1. Click reCAPTCHA checkbox (force=True to bypass overlays)
  2. If image challenge appears, switch to audio challenge
  3. Download the audio MP3
  4. Convert to WAV via pydub (requires ffmpeg)
  5. Transcribe via Google Speech Recognition (free, no API key)
  6. Submit the transcription as the answer

Cost: $0/month — fully self-hosted
Dependencies: pydub, SpeechRecognition, ffmpeg (system package)
"""

import logging
import os
import random
import tempfile
import time
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)


class RecaptchaAudioSolver:
    """Solve reCAPTCHA v2 via audio challenge + speech recognition."""

    MAX_ATTEMPTS = 3
    AUDIO_TIMEOUT = 10

    def __init__(self, page):
        """
        Args:
            page: A Playwright Page object with the reCAPTCHA-protected page loaded.
        """
        self.page = page

    def solve(self) -> bool:
        """
        Attempt to solve the reCAPTCHA on the page.
        Returns True if solved, False if failed.
        """
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            logger.info(f"[reCAPTCHA] Attempt {attempt}/{self.MAX_ATTEMPTS}")

            try:
                # Step 1: Find and click the reCAPTCHA checkbox
                if not self._click_checkbox():
                    logger.warning("[reCAPTCHA] Could not find checkbox iframe")
                    continue

                time.sleep(2)

                # Check if already solved (sometimes clicking is enough)
                if self._is_solved():
                    logger.info("[reCAPTCHA] Solved by checkbox click alone! ✅")
                    return True

                # Step 2: Switch to audio challenge
                if not self._switch_to_audio():
                    logger.warning("[reCAPTCHA] Could not switch to audio challenge")
                    continue

                time.sleep(3)

                # Step 3: Download and transcribe the audio
                transcript = self._download_and_transcribe()
                if not transcript:
                    logger.warning("[reCAPTCHA] Audio transcription failed")
                    self._get_new_challenge()
                    continue

                # Step 4: Submit the answer
                if self._submit_audio_answer(transcript):
                    logger.info("[reCAPTCHA] Audio challenge solved! ✅")
                    return True
                else:
                    logger.warning("[reCAPTCHA] Wrong audio answer, retrying...")
                    self._get_new_challenge()
                    time.sleep(1)

            except Exception as e:
                logger.warning(f"[reCAPTCHA] Attempt {attempt} error: {e}")

        logger.error("[reCAPTCHA] Failed after all attempts")
        return False

    def _get_anchor_frame(self):
        """Find the reCAPTCHA anchor iframe (checkbox)."""
        for frame in self.page.frames:
            if "recaptcha" in frame.url and "anchor" in frame.url:
                return frame
        return None

    def _get_bframe(self):
        """Find the reCAPTCHA bframe iframe (challenge)."""
        for frame in self.page.frames:
            if "recaptcha" in frame.url and "bframe" in frame.url:
                return frame
        return None

    def _click_checkbox(self) -> bool:
        """Find the reCAPTCHA anchor iframe and click the checkbox.
        
        Uses force=True to bypass overlay divs that intercept pointer events.
        Also uses JavaScript click as ultimate fallback.
        """
        try:
            frame = self._get_anchor_frame()
            if not frame:
                return False

            checkbox = frame.query_selector("#recaptcha-anchor")
            if not checkbox:
                checkbox = frame.query_selector(".recaptcha-checkbox-border")
            if not checkbox:
                return False

            # Try normal click with force=True (bypasses overlay check)
            try:
                checkbox.click(force=True, timeout=5000)
                logger.info("[reCAPTCHA] Clicked checkbox (force)")
                return True
            except Exception:
                pass

            # Fallback: JavaScript click (always works, ignores overlays)
            try:
                frame.evaluate("""() => {
                    var cb = document.querySelector('#recaptcha-anchor');
                    if (cb) cb.click();
                }""")
                logger.info("[reCAPTCHA] Clicked checkbox (JS)")
                return True
            except Exception as e:
                logger.warning(f"[reCAPTCHA] JS checkbox click failed: {e}")

        except Exception as e:
            logger.warning(f"[reCAPTCHA] Checkbox click error: {e}")
        return False

    def _is_solved(self) -> bool:
        """Check if reCAPTCHA is already solved (green checkmark)."""
        try:
            frame = self._get_anchor_frame()
            if frame:
                anchor = frame.query_selector("#recaptcha-anchor")
                if anchor:
                    aria = anchor.get_attribute("aria-checked")
                    if aria == "true":
                        return True
        except Exception:
            pass

        # Also check if g-recaptcha-response has a value
        try:
            token = self.page.evaluate("""() => {
                var el = document.querySelector('#g-recaptcha-response')
                    || document.querySelector('[name="g-recaptcha-response"]');
                return el ? el.value : '';
            }""")
            if token and len(token) > 20:
                return True
        except Exception:
            pass

        return False

    def _switch_to_audio(self) -> bool:
        """Click the audio challenge button in the reCAPTCHA bframe."""
        try:
            frame = self._get_bframe()
            if not frame:
                logger.warning("[reCAPTCHA] No bframe found")
                return False

            audio_btn = frame.query_selector("#recaptcha-audio-button")
            if audio_btn:
                try:
                    audio_btn.click(force=True, timeout=5000)
                except Exception:
                    frame.evaluate("() => { var b = document.querySelector('#recaptcha-audio-button'); if (b) b.click(); }")
                logger.info("[reCAPTCHA] Switched to audio challenge")
                time.sleep(3)
                return True
            else:
                logger.warning("[reCAPTCHA] No audio button found")
        except Exception as e:
            logger.warning(f"[reCAPTCHA] Audio switch error: {e}")
        return False

    def _download_and_transcribe(self) -> Optional[str]:
        """Download the audio challenge MP3 and transcribe it."""
        try:
            frame = self._get_bframe()
            if not frame:
                return None

            # Try multiple methods to find the audio URL
            audio_url = None

            # Method 1: Download link
            try:
                link = frame.query_selector(".rc-audiochallenge-tdownload-link")
                if link:
                    audio_url = link.get_attribute("href")
                    if audio_url:
                        logger.info("[reCAPTCHA] Found audio via download link")
            except Exception:
                pass

            # Method 2: Audio source element
            if not audio_url:
                try:
                    audio_url = frame.evaluate("""() => {
                        var src = document.querySelector('audio source');
                        if (src) return src.src || src.getAttribute('src');
                        var audio = document.querySelector('audio');
                        if (audio) return audio.src || audio.getAttribute('src');
                        var el = document.querySelector('#audio-source');
                        if (el) return el.src || el.getAttribute('src');
                        return '';
                    }""")
                    if audio_url:
                        logger.info("[reCAPTCHA] Found audio via <audio> element")
                except Exception:
                    pass

            # Method 3: Look for any audio-related link in the challenge div
            if not audio_url:
                try:
                    audio_url = frame.evaluate("""() => {
                        var links = document.querySelectorAll('a[href*="recaptcha/api2/payload"]');
                        for (var i = 0; i < links.length; i++) {
                            var href = links[i].href;
                            if (href && href.indexOf('payload') > -1) return href;
                        }
                        var links2 = document.querySelectorAll('.rc-audiochallenge-play-button a, .rc-audiochallenge-download-link');
                        for (var i = 0; i < links2.length; i++) {
                            if (links2[i].href) return links2[i].href;
                        }
                        return '';
                    }""")
                    if audio_url:
                        logger.info("[reCAPTCHA] Found audio via payload link")
                except Exception:
                    pass

            # Method 4: Press play and intercept network requests
            if not audio_url:
                try:
                    # Click the play button
                    play_btn = frame.query_selector(".rc-audiochallenge-play-button, button.rc-button-audio")
                    if play_btn:
                        play_btn.click(force=True)
                        time.sleep(2)
                        # Re-check for audio element after play
                        audio_url = frame.evaluate("""() => {
                            var audio = document.querySelector('audio');
                            if (audio && audio.src) return audio.src;
                            var src = document.querySelector('audio source');
                            if (src) return src.src || src.getAttribute('src');
                            return '';
                        }""")
                        if audio_url:
                            logger.info("[reCAPTCHA] Found audio after clicking play")
                except Exception:
                    pass

            if not audio_url:
                # Dump what we see for debugging
                debug = frame.evaluate("() => document.body.innerHTML.substring(0, 500)")
                logger.warning(f"[reCAPTCHA] No audio URL found. Frame HTML: {debug[:300]}")
                return None

            logger.info(f"[reCAPTCHA] Downloading audio: {audio_url[:80]}...")

            # Download MP3
            tmp_dir = tempfile.mkdtemp()
            mp3_path = os.path.join(tmp_dir, f"captcha_{random.randint(1000,9999)}.mp3")
            wav_path = mp3_path.replace(".mp3", ".wav")

            urllib.request.urlretrieve(audio_url, mp3_path)

            # Convert to WAV using pydub
            import pydub
            sound = pydub.AudioSegment.from_mp3(mp3_path)
            sound.export(wav_path, format="wav")

            # Transcribe using Google Speech Recognition (free)
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)
                transcript = recognizer.recognize_google(audio_data)

            logger.info(f"[reCAPTCHA] Audio transcript: '{transcript}'")

            # Cleanup
            try:
                os.remove(mp3_path)
                os.remove(wav_path)
                os.rmdir(tmp_dir)
            except Exception:
                pass

            return transcript.strip()

        except Exception as e:
            logger.warning(f"[reCAPTCHA] Audio transcription error: {e}")
            return None

    def _submit_audio_answer(self, answer: str) -> bool:
        """Type the transcribed answer and verify."""
        try:
            frame = self._get_bframe()
            if not frame:
                return False

            input_field = frame.query_selector("#audio-response")
            if input_field:
                input_field.fill(answer)
                time.sleep(0.5)

                verify_btn = frame.query_selector("#recaptcha-verify-button")
                if verify_btn:
                    verify_btn.click(force=True)
                    time.sleep(4)

                    if self._is_solved():
                        return True

                    # Check for error
                    error = frame.query_selector(".rc-audiochallenge-error-message")
                    if error:
                        error_text = error.inner_text()
                        if error_text and "Multiple" in error_text:
                            logger.warning("[reCAPTCHA] Rate limited — too many attempts")
                            return False
        except Exception as e:
            logger.warning(f"[reCAPTCHA] Submit error: {e}")
        return False

    def _get_new_challenge(self):
        """Click the reload button to get a new audio challenge."""
        try:
            frame = self._get_bframe()
            if frame:
                reload_btn = frame.query_selector("#recaptcha-reload-button")
                if reload_btn:
                    reload_btn.click(force=True)
                    time.sleep(3)
        except Exception:
            pass
