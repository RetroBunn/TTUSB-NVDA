import ctypes
import queue
import struct
import threading
import unicodedata
from collections import OrderedDict
from typing import NamedTuple

import hwPortUtils
import queueHandler
from autoSettingsUtils.driverSetting import (
	BooleanDriverSetting,
	DriverSetting,
	NumericDriverSetting,
)
from autoSettingsUtils.utils import StringParameterInfo
from logHandler import log
from speech.commands import BreakCommand, IndexCommand, PitchCommand
from synthDriverHandler import (
	LanguageInfo,
	SynthDriver,
	VoiceInfo,
	getSynth,
	setSynth,
	synthDoneSpeaking,
	synthIndexReached,
)

_USB_ID = "VID_0DD0&PID_1002"

_DLL_NAME = "ttusbd64.dll" if struct.calcsize("P") == 8 else "ttusbd.dll"

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetModuleHandleW.restype = ctypes.c_void_p
_kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
_kernel32.FreeLibrary.restype = ctypes.c_int
_kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]

_CMD = 0x01
#: Unlike every other command these carry no command prefix.
_SUSPEND = 0x10
_RESUME = 0x12
_STOP = 0x18

_SENTINEL = 99
#: Marker appended to configuration writes. It comes back only once the unit has
#: consumed everything ahead of it, which is how the driver knows the configuration
#: actually took effect rather than still sitting in a buffer that Stop can flush.
_CONFIG_MARKER = 98

_POLL_INTERVAL = 0.01

_FALLBACK_SYNTH = "oneCore"

_POR = 208

# The speed command exposes two overlapping scales: 0-9 covers the whole range (3.92s
# down to 1.48s on a fixed phrase) while 10-13 interleave into the fast end (10S=1.88,
# 11S=1.78, 12S=1.56, 13S=1.48). Ordered by measured speed, dropping the duplicates
# (11 matches 8, 13 matches 9), this leaves twelve rates that rise monotonically.
_RATE_TABLE = (0, 1, 2, 3, 4, 5, 6, 7, 10, 8, 12, 9)
_RATE_STEP = 9

_VOLUME_MAX = 9
_PITCH_MAX = 99
_INFLECTION_MAX = 9
_ARTICULATION_MAX = 9
_FORMANT_MAX = 99
_REVERB_MAX = 9
_TONE_MAX = 2
_TEXT_DELAY_MAX = 15

#The unit has no pause command, but the sinusoidal tone generator takes a duration in 10 ms units from 1 to 59999, and both tone frequencies at 0 Hz produce silence.
_SILENCE_UNIT_MS = 10
_SILENCE_MAX_UNITS = 59999


class _Voice(NamedTuple):
	name: str
	pitch: int
	inflection: int
	formant: int
	tone: int
	articulation: int
	reverb: int
	textDelay: int


# Voices and their preset values, calibrated using the Interrogation command in the RC8660 to read their exact values.
# Layout goes like this; voice name, pitch, inflection/expression, formant frequency, tone, articulation, reverb, text delay.
_VOICES = (
	_Voice("Perfect Paul", 50, 5, 50, 1, 5, 0, 0),
	_Voice("Vader", 10, 6, 40, 1, 4, 3, 0),
	_Voice("Big Bob", 40, 5, 46, 0, 5, 0, 0),
	_Voice("Precise Pete", 60, 4, 52, 2, 6, 0, 0),
	_Voice("Ricochet Randy", 40, 5, 50, 1, 5, 9, 0),
	_Voice("Biff", 50, 7, 40, 0, 6, 0, 0),
	_Voice("Skip", 15, 7, 59, 0, 6, 0, 0),
	_Voice("Robo Robert", 80, 0, 54, 1, 5, 6, 1),
	_Voice("Goliath", 20, 5, 15, 1, 5, 2, 0),
	_Voice("Alvin", 60, 5, 98, 2, 5, 0, 0),
	_Voice("Gretchen", 99, 5, 67, 2, 5, 0, 0),
)

# Default values upon first use
_DEFAULT_VOICE = 0
_DEFAULT_RATE_INDEX = _RATE_TABLE.index(5)
_DEFAULT_VOLUME = 5

# Spanish support
_LANGUAGES = OrderedDict((("en", False), ("es", True)))
_DEFAULT_LANGUAGE = "en"


_HIGH_BYTES = {
	"ñ": b"\xf1",
	"Ñ": b"\xd1",
}

_TRANSLITERATIONS = {
	ord("‘"): "'",
	ord("’"): "'",
	ord("“"): '"',
	ord("”"): '"',
	ord("–"): "-",
	ord("—"): "-",
	ord("…"): "...",
	ord(" "): " ",
}


def _command(value: int, letter: str) -> bytes:
	return f"\x01{value}{letter}\x00".encode("ascii")


def _silence(milliseconds: int) -> bytes:
	units = max(1, min(_SILENCE_MAX_UNITS, round(milliseconds / _SILENCE_UNIT_MS)))
	return f"\x01{units}j00000000\x00".encode("ascii")


def _asIndex(value: str, maximum: int, fallback: int) -> int:
	try:
		index = int(value)
	except (TypeError, ValueError):
		return fallback
	return index if 0 <= index <= maximum else fallback


def _encodeText(
	text: str,
	dictionaryEnabled: bool = False,
	allowCommands: bool = False,
) -> bytes:
	"""Render text as bytes the unit will speak rather than obey.

	The command character has to be doubled to be spoken literally, and the bare
	control bytes must not reach the unit or they would stop or suspend speech.
	With allowCommands set, neither is filtered and anything embedded in the text
	is executed by the unit instead.
	"""
	out = bytearray()
	for char in text.translate(_TRANSLITERATIONS):
		high = _HIGH_BYTES.get(char) if dictionaryEnabled else None
		if high is not None:
			out += high
			continue
		# Decompose so accented letters degrade to their base letter rather than
		# being dropped outright by the ASCII encoding.
		for byte in unicodedata.normalize("NFKD", char).encode("ascii", "ignore"):
			if allowCommands:
				out.append(byte)
			elif byte == _CMD:
				out += b"\x01\x01"
			elif byte < 0x20:
				out.append(0x20)
			else:
				out.append(byte)
	return bytes(out)


class SynthDriver(SynthDriver):
	name = "tripletalk"
	description = "TripleTalk USB"

	supportedSettings = (
		SynthDriver.VoiceSetting(),
		SynthDriver.LanguageSetting(),
		SynthDriver.RateSetting(minStep=_RATE_STEP),
		SynthDriver.PitchSetting(),
		SynthDriver.InflectionSetting(minStep=10),
		SynthDriver.VolumeSetting(minStep=10),
		NumericDriverSetting(
			"articulation",
			_("&Articulation"),
			minStep=10,
			defaultVal=_VOICES[_DEFAULT_VOICE].articulation * 10,
			availableInSettingsRing=True,
		),
		NumericDriverSetting(
			"formant",
			_("&Formant frequency"),
			defaultVal=_VOICES[_DEFAULT_VOICE].formant,
			availableInSettingsRing=True,
		),
		NumericDriverSetting(
			"reverb",
			_("Re&verb"),
			minStep=10,
			defaultVal=_VOICES[_DEFAULT_VOICE].reverb * 10,
			availableInSettingsRing=True,
		),
		DriverSetting(
			"textDelay",
			_("Text &delay"),
			defaultVal=str(_VOICES[_DEFAULT_VOICE].textDelay),
			availableInSettingsRing=True,
		),
		DriverSetting(
			"tone",
			_("&Tone"),
			defaultVal=str(_VOICES[_DEFAULT_VOICE].tone),
			availableInSettingsRing=True,
		),
		BooleanDriverSetting(
			"allowCommands",
			_("Allow co&mmands in text"),
			defaultVal=False,
			availableInSettingsRing=True,
		),
	)
	supportedCommands = {BreakCommand, IndexCommand, PitchCommand}  # noqa: RUF012
	supportedNotifications = {synthIndexReached, synthDoneSpeaking}  # noqa: RUF012

	@classmethod
	def check(cls) -> bool:
		# Deliberately does not load the DLL: it binds at load and never rebinds, so
		# loading it while the unit is unplugged would poison it for the whole session.
		try:
			return any(
				device.get("usbID", "").upper() == _USB_ID
				for device in hwPortUtils.listUsbDevices()
			)
		except Exception:
			log.debugWarning("Error enumerating USB devices", exc_info=True)
			return False

	def __init__(self):
		super().__init__()
		self._dll = None
		self._dllLock = threading.Lock()
		self._stateLock = threading.Lock()
		self._rateIndex = _DEFAULT_RATE_INDEX
		self._volume = _DEFAULT_VOLUME
		self._adoptVoice(_DEFAULT_VOICE)
		self._indexMap = {}
		self._nextSlot = 0
		self._generation = 0
		self._fellBack = False
		self._paused = False
		self._devicePitchRaised = False
		self._configPending = False
		self._language = _DEFAULT_LANGUAGE
		self._allowCommands = False
		self._queue = queue.Queue()
		self._stopped = threading.Event()
		self._load()
		self._writer = threading.Thread(
			target=self._writerLoop,
			name="TripleTalk writer",
			daemon=True,
		)
		self._reader = threading.Thread(
			target=self._readerLoop,
			name="TripleTalk reader",
			daemon=True,
		)
		self._writer.start()
		self._reader.start()
		self._enqueue(self._preamble(), pitchRaised=False, supersedes=True)

	def _load(self):
		dll = ctypes.CDLL(_DLL_NAME)
		dll.USBTT_CheckWdmStatus.restype = ctypes.c_int
		dll.USBTT_CheckWdmStatus.argtypes = []
		dll.USBTT_ReadByte.restype = ctypes.c_int
		dll.USBTT_ReadByte.argtypes = []
		dll.USBTT_WriteString.restype = ctypes.c_int
		dll.USBTT_WriteString.argtypes = [ctypes.c_char_p, ctypes.c_int]
		dll.USBTT_WriteByteImmediate.restype = None
		dll.USBTT_WriteByteImmediate.argtypes = [ctypes.c_int]
		if not dll.USBTT_CheckWdmStatus():
			self._unloadHandle(dll._handle)
			raise RuntimeError("TripleTalk is not responding; check that it is connected")
		self._dll = dll

	@staticmethod
	def _unloadHandle(handle):
		for _ in range(16):
			if not _kernel32.GetModuleHandleW(_DLL_NAME):
				return
			if not _kernel32.FreeLibrary(handle):
				return

	def _rebind(self) -> bool:
		"""Unload and reload the DLL so it binds to the unit again.

		It establishes its handle once, at load, so a unit that was unplugged or power
		cycled leaves it permanently dead. The Mini is bus powered and power cycles
		whenever the machine sleeps, making this the ordinary recovery path.

		Callers must hold the DLL lock, and no other thread may be inside the DLL.
		"""
		dll, self._dll = self._dll, None
		if dll is not None:
			handle = dll._handle
			del dll
			self._unloadHandle(handle)
		try:
			self._load()
		except Exception:
			log.debugWarning("Could not rebind to the TripleTalk", exc_info=True)
			return False
		log.debug("Rebound to the TripleTalk after it was reconnected")
		return True

	def _fallBack(self):
		"""Hand speech to another synthesizer once the unit has gone.

		Dispatched to the main thread because switching terminates this driver, which
		joins the very thread that detected the failure.
		"""
		if self._fellBack:
			return
		self._fellBack = True
		log.warning(f"TripleTalk disconnected, falling back to {_FALLBACK_SYNTH}")
		queueHandler.queueFunction(queueHandler.eventQueue, self._switchSynth)

	def _switchSynth(self):
		if getSynth() is not self:
			return
		# isFallback leaves the configured synthesizer as this one, so NVDA comes back
		# to the TripleTalk on restart rather than silently adopting the fallback.
		setSynth(_FALLBACK_SYNTH, isFallback=True)

	def _immediate(self, byte: int):
		# Timed acquire so that stopping speech cannot itself block behind a write
		# waiting for room in the unit's buffer.
		if not self._dllLock.acquire(timeout=0.1):
			log.debugWarning(f"TripleTalk busy, skipped immediate byte {byte:#04x}")
			return
		try:
			if self._dll is not None:
				self._dll.USBTT_WriteByteImmediate(byte)
		except OSError:
			log.debugWarning("Immediate write to the TripleTalk failed", exc_info=True)
		finally:
			self._dllLock.release()

	def _enqueue(
		self,
		data: bytes,
		pitchRaised: bool | None = None,
		cancellable: bool = False,
		supersedes: bool = False,
	):
		"""Queue bytes along with the pitch state they leave behind.

		The state is applied only once the write succeeds, because cancel() discards
		queued speech and a state tracked at compose time could describe bytes the
		unit never received.

		Only speech is cancellable. Configuration carries no generation and survives
		cancel(): NVDA cancels speech immediately before changing voice and again to
		announce the new one, so a cancellable voice block is routinely discarded
		before it can be written, leaving the unit on the previous voice's settings.
		"""
		if cancellable:
			self._queue.put((self._generation, data, pitchRaised))
			return
		with self._stateLock:
			self._configPending = True
			if supersedes:
				self._dropRedundantConfig()
		self._queue.put((None, data + _command(_CONFIG_MARKER, "i"), pitchRaised))

	def _dropRedundantConfig(self):
		"""Discard queued configuration that a full restatement makes redundant.

		Only the run at the tail goes. Configuration sitting before queued speech still
		has to reach the unit, or that speech would be spoken with the old settings.
		Without this, holding an arrow key through the voice list queues a block and a
		post-cancel restatement per step, and speech waits behind all of them.
		"""
		items = []
		while True:
			try:
				items.append(self._queue.get_nowait())
			except queue.Empty:
				break
		while items and items[-1] is not None and items[-1][0] is None:
			items.pop()
		for item in items:
			self._queue.put(item)

	def _writerLoop(self):
		while True:
			item = self._queue.get()
			if item is None:
				return
			generation, data, pitchRaised = item
			with self._dllLock:
				if generation is not None and generation != self._generation:
					continue
				if self._write(data):
					if pitchRaised is not None:
						self._devicePitchRaised = pitchRaised
					continue
				if not self._rebind():
					self._fallBack()
					continue
				# Rebinding leaves the unit at greeting defaults, so the preamble has
				# to go out again ahead of whatever we were trying to say.
				if (
					generation is None or generation == self._generation
				) and self._write(self._preamble() + data):
					self._devicePitchRaised = bool(pitchRaised)

	def _write(self, data: bytes) -> bool:
		if self._dll is None:
			return False
		try:
			return bool(self._dll.USBTT_WriteString(data, len(data)))
		except OSError:
			log.debugWarning("Write to the TripleTalk failed", exc_info=True)
			return False

	def _readerLoop(self):
		while not self._stopped.wait(_POLL_INTERVAL):
			while True:
				with self._dllLock:
					if self._dll is None:
						break
					try:
						value = self._dll.USBTT_ReadByte()
					except OSError:
						log.debugWarning("Read from the TripleTalk failed", exc_info=True)
						break
				if value == -1:
					break
				# Notify outside the lock: handlers run arbitrary NVDA code.
				self._onMarker(value)

	def _onMarker(self, value: int):
		if value == _CONFIG_MARKER:
			with self._stateLock:
				self._configPending = False
			return
		if value == _SENTINEL:
			synthDoneSpeaking.notify(synth=self)
			return
		with self._stateLock:
			index = self._indexMap.pop(value, None)
		if index is not None:
			synthIndexReached.notify(synth=self, index=index)

	def _adoptVoice(self, index: int):
		voice = _VOICES[index]
		self._voiceIndex = index
		self._pitch = voice.pitch
		self._inflection = voice.inflection
		self._formant = voice.formant
		self._tone = voice.tone
		self._articulation = voice.articulation
		self._reverb = voice.reverb
		self._textDelay = voice.textDelay

	def _voiceBlock(self) -> bytes:
		"""Select a voice and restate every parameter the driver tracks.

		The voice command rewrites the others internally, so it has to come first.
		The text delay command doubles as the switch into text mode.
		"""
		return b"".join(
			(
				_command(self._voiceIndex, "o"),
				_command(_RATE_TABLE[self._rateIndex], "s"),
				_command(self._volume, "v"),
				_command(self._pitch, "p"),
				_command(self._inflection, "e"),
				_command(self._articulation, "a"),
				_command(self._formant, "f"),
				_command(self._reverb, "r"),
				_command(self._tone, "x"),
				_command(self._textDelay, "t"),
			),
		)

	def _dictionaryCommand(self) -> bytes:
		# Enable or disable the unit's loaded exception dictionary, for Spanish.
		return b"\x01u\x00" if _LANGUAGES[self._language] else b"\x01t\x00"

	def _preamble(self) -> bytes:
		return _command(_POR, "g") + self._voiceBlock() + self._dictionaryCommand()

	def _allocateSlot(self, index: int) -> int:
		with self._stateLock:
			slot = self._nextSlot
			self._nextSlot = (self._nextSlot + 1) % _CONFIG_MARKER
			self._indexMap[slot] = index
			return slot

	def speak(self, speechSequence):
		data = bytearray()
		pitchRaised = None
		for item in speechSequence:
			if isinstance(item, str):
				data += _encodeText(
					item,
					_LANGUAGES[self._language],
					self._allowCommands,
				)
			elif isinstance(item, IndexCommand):
				data += _command(self._allocateSlot(item.index), "i")
			elif isinstance(item, BreakCommand):
				data += _silence(item.time)
			elif isinstance(item, PitchCommand):
				# offset is 0 for the command that restores normal pitch, so both the
				# raise and the restore fall out of the same expression. The offset is
				# applied to our own pitch rather than the command's newValue, which
				# derives from config and can lag behind the selected voice.
				pitchRaised = not item.isDefault
				data += _command(
					max(0, min(_PITCH_MAX, self._pitch + item.offset)),
					"p",
				)
		data += _command(_SENTINEL, "i")
		self._enqueue(bytes(data), pitchRaised, cancellable=True)

	def cancel(self):
		with self._stateLock:
			self._generation += 1
			self._indexMap.clear()
			# Drop queued speech but keep configuration and the terminate sentinel.
			kept = []
			while True:
				try:
					item = self._queue.get_nowait()
				except queue.Empty:
					break
				if item is None or item[0] is None:
					kept.append(item)
			for item in kept:
				self._queue.put(item)
			# Stop flushes the unit's input buffer, including commands already sent but
			# not yet acted on. Configuration whose marker has not come back may have
			# just been discarded, so restate all of it; the marker on the restated copy
			# keeps this self correcting if that copy is flushed too. Pitch alone is
			# restated otherwise, or an interrupted capital would raise what follows.
			full = self._configPending
			if full:
				restore = self._voiceBlock() + self._dictionaryCommand()
			elif self._devicePitchRaised:
				restore = _command(self._pitch, "p")
			else:
				restore = b""
			resume = self._paused
			self._paused = False
		self._immediate(_STOP)
		if resume:
			# Stop does not clear a suspension, which would leave the unit silent for
			# everything that followed. Only sent when actually paused: NVDA cancels
			# on every keystroke, and a stray control byte can be read aloud.
			self._immediate(_RESUME)
		if restore:
			# Deliberately not clearing the flag here: it only changes once a write
			# lands, so a second cancel arriving first will simply queue this again.
			self._enqueue(restore, pitchRaised=False, supersedes=full)

	def pause(self, switch: bool):
		self._paused = switch
		self._immediate(_SUSPEND if switch else _RESUME)

	def terminate(self):
		self.cancel()
		super().terminate()
		self._stopped.set()
		self._queue.put(None)
		for thread in (self._writer, self._reader):
			thread.join(timeout=2)
		# Only safe once both threads have stopped: unloading the DLL while a thread
		# is inside it would take NVDA down.
		with self._dllLock:
			dll, self._dll = self._dll, None
			if dll is not None:
				handle = dll._handle
				del dll
				self._unloadHandle(handle)

	def _get_rate(self) -> int:
		return self._rateIndex * _RATE_STEP

	def _set_rate(self, value: int):
		self._rateIndex = max(0, min(len(_RATE_TABLE) - 1, value // _RATE_STEP))
		self._enqueue(_command(_RATE_TABLE[self._rateIndex], "s"))

	def _get_pitch(self) -> int:
		return self._pitch

	def _set_pitch(self, value: int):
		self._pitch = max(0, min(_PITCH_MAX, value))
		self._enqueue(_command(self._pitch, "p"), pitchRaised=False)

	def _get_volume(self) -> int:
		return self._volume * 10

	def _set_volume(self, value: int):
		self._volume = max(0, min(_VOLUME_MAX, value // 10))
		self._enqueue(_command(self._volume, "v"))

	def _get_inflection(self) -> int:
		return self._inflection * 10

	def _set_inflection(self, value: int):
		self._inflection = max(0, min(_INFLECTION_MAX, value // 10))
		self._enqueue(_command(self._inflection, "e"))

	def _get_articulation(self) -> int:
		return self._articulation * 10

	def _set_articulation(self, value: int):
		self._articulation = max(0, min(_ARTICULATION_MAX, value // 10))
		self._enqueue(_command(self._articulation, "a"))

	def _get_formant(self) -> int:
		return self._formant

	def _set_formant(self, value: int):
		self._formant = max(0, min(_FORMANT_MAX, value))
		self._enqueue(_command(self._formant, "f"))

	def _get_reverb(self) -> int:
		return self._reverb * 10

	def _set_reverb(self, value: int):
		self._reverb = max(0, min(_REVERB_MAX, value // 10))
		self._enqueue(_command(self._reverb, "r"))

	def _get_availableTones(self) -> OrderedDict:
		return OrderedDict(
			(str(value), StringParameterInfo(str(value), label))
			for value, label in enumerate((_("Bass"), _("Normal"), _("Treble")))
		)

	def _get_tone(self) -> str:
		return str(self._tone)

	def _set_tone(self, value: str):
		self._tone = _asIndex(value, _TONE_MAX, self._tone)
		self._enqueue(_command(self._tone, "x"))

	def _get_availableTextdelays(self) -> OrderedDict:
		return OrderedDict(
			(str(value), StringParameterInfo(str(value), str(value)))
			for value in range(_TEXT_DELAY_MAX + 1)
		)

	def _get_textDelay(self) -> str:
		return str(self._textDelay)

	def _set_textDelay(self, value: str):
		self._textDelay = _asIndex(value, _TEXT_DELAY_MAX, self._textDelay)
		self._enqueue(_command(self._textDelay, "t"))

	def _get_availableLanguages(self) -> OrderedDict:
		return OrderedDict((code, LanguageInfo(code)) for code in _LANGUAGES)

	def _get_allowCommands(self) -> bool:
		return self._allowCommands

	def _set_allowCommands(self, value: bool):
		self._allowCommands = bool(value)

	def _get_language(self) -> str:
		return self._language

	def _set_language(self, value: str):
		self._language = value if value in _LANGUAGES else _DEFAULT_LANGUAGE
		self._enqueue(self._dictionaryCommand())

	def _getAvailableVoices(self) -> OrderedDict:
		return OrderedDict(
			(str(index), VoiceInfo(str(index), voice.name))
			for index, voice in enumerate(_VOICES)
		)

	def _get_voice(self) -> str:
		return str(self._voiceIndex)

	def _set_voice(self, value: str):
		self._adoptVoice(_asIndex(value, len(_VOICES) - 1, self._voiceIndex))
		self._enqueue(self._voiceBlock(), pitchRaised=False, supersedes=True)
