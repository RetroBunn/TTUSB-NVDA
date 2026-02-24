# synthDrivers/tripletalk.py
#
# NVDA Synthesizer Driver for the Triple-Talk USB and USB Mini
# by RetroBunn
#
# ttusbd.dll is loaded from C:\WINDOWS, where the Triple-Talk driver installer places it.
#
# DISCLAIMER: This add-on was vibe coded with the assistance of an AI and may contain
# errors or unintended behaviour as a result. Real human contributions and bug fixes
# are welcomed — if you encounter an issue, feel free to submit a correction.
#

import os
import ctypes
import threading
import queue
import synthDriverHandler
from synthDriverHandler import SynthDriver, VoiceInfo, synthIndexReached, synthDoneSpeaking
from speech.commands import (
    IndexCommand,
    RateCommand,
    PitchCommand,
    VolumeCommand,
    BreakCommand,
)
import logging

log = logging.getLogger(__name__)


# ttusbd.dll is installed system-wide into C:\WINDOWS by the Triple-Talk driver package.
_DLL_PATH = os.path.join(os.environ.get('SystemRoot', r'C:\WINDOWS'), 'ttusbd.dll')

# Triple-Talk command prefix (Control-A)
_CMD = b'\x01'

# Ctrl-X: silence and clear input buffer
_SILENCE = 0x18

# Carriage return flushes the input buffer to speech output
_FLUSH = b'\r'


# RC8660 voices (Table 8, RC8660 manual)
_VOICES = {
    '0':  VoiceInfo('0',  'Perfect Paul'),
    '1':  VoiceInfo('1',  'Vader'),
    '2':  VoiceInfo('2',  'Big Bob'),
    '3':  VoiceInfo('3',  'Precise Pete'),
    '4':  VoiceInfo('4',  'Ricochet Randy'),
    '5':  VoiceInfo('5',  'Biff'),
    '6':  VoiceInfo('6',  'Skip'),
    '7':  VoiceInfo('7',  'Robo Robert'),
    '8':  VoiceInfo('8',  'Goliath'),
    '9':  VoiceInfo('9',  'Alvin'),
    '10': VoiceInfo('10', 'Gretchen'),
}

# Per-voice parameter baselines.
#
# Pitch and formant are raw TT values (0-99), passed directly to hardware.
# Inflection, articulation, and reverb are NVDA 0-100 scale, step-10 (tt * 10).
# Text delay is NVDA 0-100 scale, step-7 (tt * 7).
# Tone is NVDA 0-100 scale, step-50 (tt * 50): 0=Bass, 50=Normal, 100=Treble.

_VOICE_PITCH = {
    '0':  50,
    '1':  10,
    '2':  40,
    '3':  60,
    '4':  40,
    '5':  50,
    '6':  15,
    '7':  80,
    '8':  20,
    '9':  60,
    '10': 99,
}

_VOICE_FORMANT = {
    '0':  50,
    '1':  40,
    '2':  46,
    '3':  52,
    '4':  50,
    '5':  40,
    '6':  58,
    '7':  54,
    '8':  15,
    '9':  99,
    '10': 67,
}

_VOICE_INFLECTION = {
    '0':  50,
    '1':  60,
    '2':  50,
    '3':  40,
    '4':  50,
    '5':  70,
    '6':  70,
    '7':   0,
    '8':  50,
    '9':  50,
    '10': 50,
}

_VOICE_ARTICULATION = {
    '0':  50,
    '1':  40,
    '2':  50,
    '3':  60,
    '4':  40,
    '5':  60,
    '6':  60,
    '7':  40,
    '8':  50,
    '9':  50,
    '10': 50,
}

_VOICE_REVERB = {
    '0':   0,
    '1':  30,
    '2':   0,
    '3':   0,
    '4':  90,
    '5':   0,
    '6':   0,
    '7':  60,
    '8':  20,
    '9':   0,
    '10':  0,
}

_VOICE_TEXTDELAY = {
    '0':   0,
    '1':   0,
    '2':   0,
    '3':   0,
    '4':   0,
    '5':   0,
    '6':   0,
    '7':   7,
    '8':   0,
    '9':   0,
    '10':  0,
}

_VOICE_TONE = {
    '0': 50, '1': 50, '2':  0, '3': 100, '4': 50,
    '5':  0, '6':  0, '7': 50, '8': 50, '9': 100, '10': 100,
}

# Obtained at runtime so we never directly import a private NVDA class.
_NumericSynthSetting = type(SynthDriver.RateSetting())


def _loadDll():
    """
    Load ttusbd.dll from C:\\WINDOWS, where the Triple-Talk driver installer places it.
    Adds that directory to the DLL search path so Windows can resolve dependencies.
    """
    if not os.path.isfile(_DLL_PATH):
        raise FileNotFoundError(
            f"ttusbd.dll not found at: {_DLL_PATH}\n"
            "Please ensure the Triple-Talk USB driver package is installed."
        )

    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(os.path.dirname(_DLL_PATH))

    dll = ctypes.CDLL(_DLL_PATH)

    dll.USBTT_WriteByteImmediate.restype  = None
    dll.USBTT_WriteByteImmediate.argtypes = [ctypes.c_int]

    dll.USBTT_WriteString.restype  = ctypes.c_int
    dll.USBTT_WriteString.argtypes = [ctypes.c_char_p, ctypes.c_int]

    dll.USBTT_CheckWdmStatus.restype  = ctypes.c_int
    dll.USBTT_CheckWdmStatus.argtypes = []

    return dll


class SynthDriver(synthDriverHandler.SynthDriver):
    """NVDA synthesizer driver for the Triple-Talk USB and USB Mini."""

    name        = 'tripletalk'
    description = 'Triple-Talk USB/USB Mini'

    # Settings layout:
    #   Voice, Rate, Pitch, Inflection, Volume        — standard NVDA settings
    #   Articulation, Reverb                          — 0-9 range, minStep=10
    #   Formant Frequency                             — 0-99 range, passed directly
    #   Text Delay                                    — 0-15 range, minStep=7
    #   Tone                                          — combo box (Bass/Normal/Treble)
    supportedSettings = (
        SynthDriver.VoiceSetting(),
        SynthDriver.RateSetting(minStep=10),
        SynthDriver.PitchSetting(),
        SynthDriver.InflectionSetting(minStep=10),
        SynthDriver.VolumeSetting(minStep=10),
        _NumericSynthSetting('articulation', '&Articulation',    minStep=10),
        _NumericSynthSetting('reverb',       'Re&verb',          minStep=10),
        _NumericSynthSetting('formant',      'Formant Frequency'            ),
        _NumericSynthSetting('textdelay',    'Text &Delay',      minStep=7 ),
        _NumericSynthSetting('tone', '&Tone', minStep=50),
    )

    _DEFAULT_VOICE = '0'

    @classmethod
    def check(cls):
        """Return True if ttusbd.dll is present and the WDM driver is active."""
        try:
            dll = _loadDll()
            status = dll.USBTT_CheckWdmStatus()
            if status != 1:
                log.warning(
                    f"Triple-Talk: USBTT_CheckWdmStatus() returned {status} "
                    "(expected 1). Is the USB device connected?"
                )
            return status == 1
        except FileNotFoundError as e:
            log.warning(f"Triple-Talk: {e}")
            return False
        except Exception:
            log.exception("Triple-Talk: unexpected error in check()")
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self):
        self._dll        = _loadDll()
        self._terminated = False

        # Initialise all settings from the default voice's baselines.
        v = self._DEFAULT_VOICE
        self._voice        = v
        self._rate         = 30   # TT default 3S
        self._pitch        = _VOICE_PITCH[v]
        self._inflection   = _VOICE_INFLECTION[v]
        self._volume       = 50   # TT default 5V
        self._articulation = _VOICE_ARTICULATION[v]
        self._reverb       = _VOICE_REVERB[v]
        self._formant      = _VOICE_FORMANT[v]
        self._textdelay    = _VOICE_TEXTDELAY[v]
        self._tone         = _VOICE_TONE[v]

        self._queue  = queue.Queue()
        self._thread = threading.Thread(
            target=self._worker, name='TT-Worker', daemon=True
        )
        self._thread.start()

        self._sendImmediate(_CMD + b'@')   # hard reset
        self._applyAllSettings()

    def terminate(self):
        """Shut down the worker thread cleanly."""
        self._terminated = True
        self._dll.USBTT_WriteByteImmediate(_SILENCE)
        self._queue.put(None)
        self._thread.join(timeout=3.0)

    # ------------------------------------------------------------------
    # Low-level send helpers
    # ------------------------------------------------------------------

    def _write(self, data: bytes):
        """Write raw bytes to the synth."""
        self._dll.USBTT_WriteString(data, len(data))

    def _sendImmediate(self, data: bytes):
        """Write bytes immediately, bypassing the input buffer."""
        for b in data:
            self._dll.USBTT_WriteByteImmediate(b)

    def _buildCmd(self, param, letter: str) -> bytes:
        """Build a Triple-Talk command string, e.g. _buildCmd(7, 'V') -> b'\\x017V'."""
        return _CMD + str(int(param)).encode('ascii') + letter.encode('ascii')

    # ------------------------------------------------------------------
    # Settings application
    # ------------------------------------------------------------------

    def _applyAllSettings(self):
        # Voice must come first — it resets the chip's internal voice parameters.
        self._applyVoice()
        self._applyRate()
        self._applyPitch()
        self._applyInflection()
        self._applyVolume()
        self._applyArticulation()
        self._applyReverb()
        self._applyFormant()
        self._applyTextdelay()
        self._applyTone()
        # Punctuation mode 6: some punctuation spoken, numbers mode, leading-zero suppression
        self._write(_CMD + b'6B')

    def _applyVoice(self):
        self._write(_CMD + self._voice.encode('ascii') + b'O')

    def _applyRate(self):
        # NVDA 0-100 (step 10) -> TT 0-9
        self._write(self._buildCmd(min(self._rate // 10, 9), 'S'))

    def _applyPitch(self):
        # Passed directly, clamped to TT max of 99
        self._write(self._buildCmd(min(self._pitch, 99), 'P'))

    def _applyInflection(self):
        # NVDA 0-100 (step 10) -> TT 0-9
        self._write(self._buildCmd(min(self._inflection // 10, 9), 'E'))

    def _applyVolume(self):
        # NVDA 0-100 (step 10) -> TT 0-9
        self._write(self._buildCmd(min(self._volume // 10, 9), 'V'))

    def _applyArticulation(self):
        # NVDA 0-100 (step 10) -> TT 0-9
        self._write(self._buildCmd(min(self._articulation // 10, 9), 'A'))

    def _applyReverb(self):
        # NVDA 0-100 (step 10) -> TT 0-9
        self._write(self._buildCmd(min(self._reverb // 10, 9), 'R'))

    def _applyFormant(self):
        # Passed directly, clamped to TT max of 99
        self._write(self._buildCmd(min(self._formant, 99), 'F'))

    def _applyTextdelay(self):
        # NVDA 0-100 (step 7) -> TT 0-15; rounding ensures TT 15 is reachable
        self._write(self._buildCmd(min(round(self._textdelay * 15 / 100), 15), 'T'))

    def _applyTone(self):
        # NVDA 0-100 (step 50) -> TT 0-2 via integer division
        self._write(self._buildCmd(min(self._tone // 50, 2), 'X'))

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _worker(self):
        """Drain the speech queue on a background thread."""
        while True:
            item = self._queue.get()
            if item is None:
                break
            try:
                self._processSpeechSequence(item)
            except Exception:
                log.exception('Triple-Talk: error processing speech sequence')
            finally:
                self._queue.task_done()

    def _processSpeechSequence(self, sequence):
        """Convert an NVDA speech sequence into Triple-Talk commands and send to hardware."""
        buf = bytearray()

        def flush():
            nonlocal buf
            if buf:
                self._write(bytes(buf) + _FLUSH)
                buf = bytearray()

        for item in sequence:
            if isinstance(item, str):
                # Triple-Talk only handles ASCII; replace anything else with a space
                buf.extend(item.encode('ascii', errors='replace'))

            elif isinstance(item, IndexCommand):
                flush()
                self.lastIndex = item.index
                if not self._terminated:
                    synthIndexReached.notify(synth=self, index=item.index)

            elif isinstance(item, RateCommand):
                buf.extend(self._buildCmd(min(item.newValue // 10, 9), 'S'))

            elif isinstance(item, PitchCommand):
                buf.extend(self._buildCmd(min(item.newValue, 99), 'P'))

            elif isinstance(item, VolumeCommand):
                buf.extend(self._buildCmd(min(item.newValue // 10, 9), 'V'))

            elif isinstance(item, BreakCommand):
                flush()
                self._write(b', \r' if item.time <= 200 else b'. \r')

        flush()
        if not self._terminated:
            synthDoneSpeaking.notify(synth=self)

    # ------------------------------------------------------------------
    # NVDA SynthDriver API
    # ------------------------------------------------------------------

    def speak(self, speechSequence):
        self._queue.put(list(speechSequence))

    def cancel(self):
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        self._dll.USBTT_WriteByteImmediate(_SILENCE)

    def pause(self, switch):
        if switch:
            self._dll.USBTT_WriteByteImmediate(_SILENCE)

    # ------------------------------------------------------------------
    # Voice
    # ------------------------------------------------------------------

    def _get_availableVoices(self):
        return _VOICES

    def _get_voice(self):
        return self._voice

    def _set_voice(self, value):
        if value not in _VOICES:
            return
        self._voice        = value
        self._pitch        = _VOICE_PITCH[value]
        self._inflection   = _VOICE_INFLECTION[value]
        self._articulation = _VOICE_ARTICULATION[value]
        self._reverb       = _VOICE_REVERB[value]
        self._formant      = _VOICE_FORMANT[value]
        self._textdelay    = _VOICE_TEXTDELAY[value]
        self._tone         = _VOICE_TONE[value]
        self._applyVoice()
        self._applyPitch()
        self._applyInflection()
        self._applyArticulation()
        self._applyReverb()
        self._applyFormant()
        self._applyTextdelay()
        self._applyTone()

    # ------------------------------------------------------------------
    # Rate — NVDA 0-100 (step 10), TT 0-9
    # ------------------------------------------------------------------

    def _get_rate(self):        return self._rate
    def _set_rate(self, value): self._rate = value; self._applyRate()

    # ------------------------------------------------------------------
    # Pitch — passed directly to TT (0-99)
    # ------------------------------------------------------------------

    def _get_pitch(self):        return self._pitch
    def _set_pitch(self, value): self._pitch = value; self._applyPitch()

    # ------------------------------------------------------------------
    # Inflection — NVDA 0-100 (step 10), TT 0-9
    # ------------------------------------------------------------------

    def _get_inflection(self):        return self._inflection
    def _set_inflection(self, value): self._inflection = value; self._applyInflection()

    # ------------------------------------------------------------------
    # Volume — NVDA 0-100 (step 10), TT 0-9
    # ------------------------------------------------------------------

    def _get_volume(self):        return self._volume
    def _set_volume(self, value): self._volume = value; self._applyVolume()

    # ------------------------------------------------------------------
    # Articulation — NVDA 0-100 (step 10), TT 0-9
    # ------------------------------------------------------------------

    def _get_articulation(self):        return self._articulation
    def _set_articulation(self, value): self._articulation = value; self._applyArticulation()

    # ------------------------------------------------------------------
    # Reverb — NVDA 0-100 (step 10), TT 0-9
    # ------------------------------------------------------------------

    def _get_reverb(self):        return self._reverb
    def _set_reverb(self, value): self._reverb = value; self._applyReverb()

    # ------------------------------------------------------------------
    # Formant Frequency — passed directly to TT (0-99)
    # ------------------------------------------------------------------

    def _get_formant(self):        return self._formant
    def _set_formant(self, value): self._formant = value; self._applyFormant()

    # ------------------------------------------------------------------
    # Text Delay — NVDA 0-100 (step 7), TT 0-15
    # ------------------------------------------------------------------

    def _get_textdelay(self):        return self._textdelay
    def _set_textdelay(self, value): self._textdelay = value; self._applyTextdelay()

    # ------------------------------------------------------------------
    # Tone — NVDA 0-100 (step 50): 0=Bass, 50=Normal, 100=Treble
    # ------------------------------------------------------------------

    def _get_tone(self):        return self._tone
    def _set_tone(self, value): self._tone = value; self._applyTone()
