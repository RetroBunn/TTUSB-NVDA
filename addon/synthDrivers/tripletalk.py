import ctypes
import json
import queue
import struct
import threading
import unicodedata
from collections import OrderedDict
from typing import NamedTuple

import config
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

_DLL_NAME = "ttusbd64.dll" if struct.calcsize("P") == 8 else "ttusbd.dll"

#: Kernel driver behind the unit, installed as a service by the vendor INF. Keying on
#: whether it is loaded is what the vendor's own TripleTalk USB Speech Utility did.
_DRIVER_SERVICE = "TTUSB"
_SERVICE_RUNNING = 4
_ERROR_SERVICE_DOES_NOT_EXIST = 1060

_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_advapi32.OpenSCManagerW.restype = ctypes.c_void_p
_advapi32.OpenSCManagerW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
_advapi32.OpenServiceW.restype = ctypes.c_void_p
_advapi32.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
_advapi32.CloseServiceHandle.argtypes = [ctypes.c_void_p]


class _SERVICE_STATUS(ctypes.Structure):
	_fields_ = [(name, ctypes.c_uint32) for name in (
		"serviceType", "currentState", "controlsAccepted", "win32ExitCode",
		"serviceSpecificExitCode", "checkPoint", "waitHint",
	)]


_advapi32.QueryServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(_SERVICE_STATUS)]

#: Device the driver exposes, and the same path the vendor's TripleTalk USB Speech
#: Utility opens. Opening it answers presence, driver loaded and usable in one call,
#: and succeeds even while the DLL holds the device, so it is safe to ask at any time.
_DEVICE_PATH = r"\\.\ttusb-0"
_INVALID_HANDLE = ctypes.c_void_p(-1).value

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetModuleHandleW.restype = ctypes.c_void_p
_kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
_kernel32.FreeLibrary.restype = ctypes.c_int
_kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
_kernel32.CreateFileW.restype = ctypes.c_void_p
_kernel32.CreateFileW.argtypes = [
	ctypes.c_wchar_p,
	ctypes.c_uint32,
	ctypes.c_uint32,
	ctypes.c_void_p,
	ctypes.c_uint32,
	ctypes.c_uint32,
	ctypes.c_void_p,
]
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

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

#: Writes are split into chunks so that cancelling can interrupt a long utterance.
#: USBTT_WriteString returns almost immediately below roughly 25 bytes but costs about
#: 0.59 ms/byte beyond its internal buffer, because it then waits for the unit to drain,
#: so a single full line would otherwise occupy the writer for over a tenth of a second.
_WRITE_CHUNK = 64

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

# The seven values a voice preset carries, with the unit's maximum for each. Rate and
# volume are absent deliberately: selecting a voice provably leaves them alone, and the
# language and command settings describe the unit rather than a voice.
_VOICE_FIELDS = OrderedDict((
	("pitch", _PITCH_MAX),
	("inflection", _INFLECTION_MAX),
	("formant", _FORMANT_MAX),
	("tone", _TONE_MAX),
	("articulation", _ARTICULATION_MAX),
	("reverb", _REVERB_MAX),
	("textDelay", _TEXT_DELAY_MAX),
))

#: Marks a voice id as user-created; built-in ids are just the index as a string. The
#: colon is safe because NVDA builds voice dictionary filenames from the display name,
#: not the id, and filters that through api.filterFileName.
_CUSTOM_PREFIX = "custom:"

#: Custom voices live in their own section rather than among the synth's settings,
#: which are a fixed set of scalars NVDA manages itself.
_CONFIG_SECTION = "tripletalk"
config.conf.spec[_CONFIG_SECTION] = {"customVoices": "string(default='{}')"}

_VOICE_FILE_FORMAT = "tripletalk-voice"
_VOICE_FILE_VERSION = 1


class VoiceStoreError(Exception):
	"""A custom voice could not be read, written or understood."""


class _CustomVoice(NamedTuple):
	base: int
	params: dict

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


def _deviceOpens() -> bool:
	"""Whether the unit's device can be opened, i.e. it is present and driven."""
	handle = _kernel32.CreateFileW(
		_DEVICE_PATH,
		0,  # query access only; enough to prove the device exists
		3,  # FILE_SHARE_READ | FILE_SHARE_WRITE
		None,
		3,  # OPEN_EXISTING
		0,
		None,
	)
	if handle == _INVALID_HANDLE:
		return False
	_kernel32.CloseHandle(ctypes.c_void_p(handle))
	return True


def _driverState() -> str:
	"""Report the kernel driver as one of "running", "stopped" or "absent".

	Reads the service manager only, so unlike loading the DLL this cannot bind to the
	device and cannot poison the handle for the rest of the session.
	"""
	scm = _advapi32.OpenSCManagerW(None, None, 0x0001)  # SC_MANAGER_CONNECT
	if not scm:
		return "absent"
	try:
		service = _advapi32.OpenServiceW(
			ctypes.c_void_p(scm),
			_DRIVER_SERVICE,
			0x0004,  # SERVICE_QUERY_STATUS
		)
		if not service:
			if ctypes.get_last_error() != _ERROR_SERVICE_DOES_NOT_EXIST:
				log.debugWarning(f"Could not query {_DRIVER_SERVICE}: {ctypes.WinError()}")
			return "absent"
		try:
			status = _SERVICE_STATUS()
			if not _advapi32.QueryServiceStatus(ctypes.c_void_p(service), ctypes.byref(status)):
				return "absent"
			return "running" if status.currentState == _SERVICE_RUNNING else "stopped"
		finally:
			_advapi32.CloseServiceHandle(ctypes.c_void_p(service))
	finally:
		_advapi32.CloseServiceHandle(ctypes.c_void_p(scm))


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


def _cleanVoice(record) -> _CustomVoice:
	"""Validate one stored or imported voice, clamping it to what the unit accepts.

	A parameter that is missing falls back to the base voice's own value, so a file
	written by a future version with extra fields still loads.
	"""
	if not isinstance(record, dict):
		raise VoiceStoreError("a voice must be an object")
	base = _asIndex(record.get("base", _DEFAULT_VOICE), len(_VOICES) - 1, _DEFAULT_VOICE)
	params = record.get("params")
	if not isinstance(params, dict):
		raise VoiceStoreError("the voice has no parameters")
	cleaned = {}
	for field, maximum in _VOICE_FIELDS.items():
		raw = params.get(field, getattr(_VOICES[base], field))
		try:
			cleaned[field] = max(0, min(maximum, int(raw)))
		except (TypeError, ValueError):
			raise VoiceStoreError(f"{field} is not a number")
	return _CustomVoice(base, cleaned)


def loadCustomVoices() -> OrderedDict:
	"""Every saved voice as {name: _CustomVoice}, ordered by name."""
	try:
		stored = json.loads(config.conf[_CONFIG_SECTION]["customVoices"])
	except Exception:
		log.debugWarning("Could not read the saved TripleTalk voices", exc_info=True)
		return OrderedDict()
	voices = OrderedDict()
	if isinstance(stored, dict):
		for name in sorted(stored, key=str.lower):
			try:
				voices[str(name)] = _cleanVoice(stored[name])
			except VoiceStoreError:
				log.debugWarning(f"Ignoring unreadable saved voice {name!r}")
	return voices


def _storeCustomVoices(voices):
	config.conf[_CONFIG_SECTION]["customVoices"] = json.dumps(
		{name: {"base": v.base, "params": v.params} for name, v in voices.items()},
		sort_keys=True,
	)


def saveCustomVoice(name: str, base: int, params: dict) -> str:
	"""Create or overwrite a voice, returning the name it was stored under."""
	name = (name or "").strip()
	if not name:
		raise VoiceStoreError("the voice needs a name")
	voices = loadCustomVoices()
	voices[name] = _cleanVoice({"base": base, "params": params})
	_storeCustomVoices(voices)
	return name


def deleteCustomVoice(name: str):
	voices = loadCustomVoices()
	if voices.pop(name, None) is not None:
		_storeCustomVoices(voices)


def exportCustomVoice(name: str, path: str):
	voice = loadCustomVoices().get(name)
	if voice is None:
		raise VoiceStoreError(f"there is no voice named {name!r}")
	document = {
		"format": _VOICE_FILE_FORMAT,
		"version": _VOICE_FILE_VERSION,
		"name": name,
		"base": voice.base,
		# Informational, for anyone reading the file: the import ignores it.
		"baseName": _VOICES[voice.base].name,
		"params": voice.params,
	}
	with open(path, "w", encoding="utf-8") as f:
		json.dump(document, f, indent=2, sort_keys=True)


def importCustomVoice(path: str) -> str:
	"""Read a voice file, returning the name it was saved under."""
	try:
		with open(path, encoding="utf-8") as f:
			document = json.load(f)
	except (OSError, ValueError) as e:
		raise VoiceStoreError(f"could not read the voice file: {e}")
	if not isinstance(document, dict) or document.get("format") != _VOICE_FILE_FORMAT:
		raise VoiceStoreError("that is not a TripleTalk voice file")
	try:
		version = int(document.get("version", 0))
	except (TypeError, ValueError):
		version = 0
	if version > _VOICE_FILE_VERSION:
		raise VoiceStoreError("that voice file needs a newer version of this add-on")
	voice = _cleanVoice(document)
	voices = loadCustomVoices()
	wanted = str(document.get("name", "")).strip() or "Imported voice"
	name, suffix = wanted, 2
	while name in voices:
		name = f"{wanted} ({suffix})"
		suffix += 1
	voices[name] = voice
	_storeCustomVoices(voices)
	return name


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
		"""Report whether the unit is present with its driver loaded.

		Opening the device is what the vendor's own TripleTalk USB Speech Utility
		does, and it answers presence, driver loaded and usable in a single call. It
		never loads the DLL, which binds at load and never rebinds, so asking here
		cannot poison the handle for the rest of the session.

		The service is consulted only to explain a failure, never to decide one.
		"""
		if _deviceOpens():
			return True
		state = _driverState()
		if state != "running":
			log.warning(
				f"The TripleTalk driver {_DRIVER_SERVICE} is "
				+ ("not installed" if state == "absent" else "not running")
				+ "; the synthesizer will not be offered",
			)
		return False

	def __init__(self):
		super().__init__()
		self._dll = None
		self._dllLock = threading.Lock()
		self._bindLock = threading.Lock()
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

		Takes both locks: no other thread may be inside the DLL while it is swapped.
		"""
		with self._dllLock, self._bindLock:
			return self._swapDll()

	def _swapDll(self) -> bool:
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
		# Deliberately not the DLL lock. A long utterance holds that for over a tenth
		# of a second, and stopping speech has to reach the unit now, not after it.
		# The bind lock is held only while the DLL handle is swapped, so this waits
		# for practical purposes never.
		with self._bindLock:
			if self._dll is None:
				return
			try:
				self._dll.USBTT_WriteByteImmediate(byte)
			except OSError:
				log.debugWarning("Immediate write to the TripleTalk failed", exc_info=True)

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
			if generation is not None and generation != self._generation:
				continue
			if self._write(data, generation):
				if pitchRaised is not None:
					self._devicePitchRaised = pitchRaised
				continue
			if not self._rebind():
				self._fallBack()
				continue
			# Rebinding leaves the unit at greeting defaults, so the preamble has to go
			# out again ahead of whatever we were trying to say.
			if (
				generation is None or generation == self._generation
			) and self._write(self._preamble() + data):
				self._devicePitchRaised = bool(pitchRaised)

	def _write(self, data: bytes, generation: int | None = None) -> bool:
		"""Push bytes to the unit, in chunks so a cancel can interrupt a long utterance.

		Returns False only on device failure. An interrupted write reports success:
		nothing is wrong with the unit, so there is nothing to recover from.
		"""
		for start in range(0, len(data), _WRITE_CHUNK):
			if generation is not None and generation != self._generation:
				return True
			chunk = data[start : start + _WRITE_CHUNK]
			with self._dllLock:
				if self._dll is None:
					return False
				try:
					if not self._dll.USBTT_WriteString(chunk, len(chunk)):
						return False
				except OSError:
					log.debugWarning("Write to the TripleTalk failed", exc_info=True)
					return False
		return True

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
		"""Switch to a built-in voice, taking its preset values."""
		voice = _VOICES[index]
		self._applyVoice(str(index), index, {f: getattr(voice, f) for f in _VOICE_FIELDS})

	def _adoptCustomVoice(self, name: str, voice: _CustomVoice):
		self._applyVoice(_CUSTOM_PREFIX + name, voice.base, voice.params)

	def _applyVoice(self, voiceId: str, base: int, params: dict):
		"""Record the voice NVDA sees, the base the unit is told, and the parameters.

		A custom voice keeps a base of its own because selecting one alters more than
		the seven values this driver can address: voices still sound distinct when
		those seven are made identical, so the base cannot be inferred from them.
		"""
		self._voiceId = voiceId
		self._voiceIndex = base
		for field in _VOICE_FIELDS:
			setattr(self, "_" + field, params[field])

	def _currentParams(self) -> dict:
		return {field: getattr(self, "_" + field) for field in _VOICE_FIELDS}

	def currentVoiceDefinition(self):
		"""The live voice as (base index, parameters), for saving it under a name."""
		return self._voiceIndex, self._currentParams()

	def _voiceBlock(self) -> bytes:
		return self._voiceBlockFor(self._voiceIndex, self._currentParams())

	def _voiceBlockFor(self, base: int, params: dict) -> bytes:
		"""Select a voice and restate every parameter the driver tracks.

		The voice command rewrites the others internally, so it has to come first.
		The text delay command doubles as the switch into text mode.
		"""
		return b"".join(
			(
				_command(base, "o"),
				_command(_RATE_TABLE[self._rateIndex], "s"),
				_command(self._volume, "v"),
				_command(params["pitch"], "p"),
				_command(params["inflection"], "e"),
				_command(params["articulation"], "a"),
				_command(params["formant"], "f"),
				_command(params["reverb"], "r"),
				_command(params["tone"], "x"),
				_command(params["textDelay"], "t"),
			),
		)

	def previewVoice(self, base: int, params: dict, text: str):
		"""Speak a sample in a voice that may not be saved, then restore the real one.

		The restoring block is configuration, so cancelling the preview cannot discard
		it and leave the unit on the previewed settings.
		"""
		voice = _cleanVoice({"base": base, "params": params})
		data = bytearray(self._voiceBlockFor(voice.base, voice.params))
		data += _encodeText(text, _LANGUAGES[self._language], self._allowCommands)
		data += _command(_SENTINEL, "i")
		self.cancel()
		self._enqueue(bytes(data), cancellable=True)
		self._enqueue(self._voiceBlock(), pitchRaised=False)

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
		with self._dllLock, self._bindLock:
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
		voices = OrderedDict(
			(str(index), VoiceInfo(str(index), voice.name))
			for index, voice in enumerate(_VOICES)
		)
		for name in loadCustomVoices():
			voiceId = _CUSTOM_PREFIX + name
			voices[voiceId] = VoiceInfo(voiceId, name)
		return voices

	def _get_availableVoices(self) -> OrderedDict:
		# Rebuilt on every query rather than cached once by the base class, because
		# voices can be created and deleted while the synth is running.
		return self._getAvailableVoices()

	def _get_voice(self) -> str:
		return self._voiceId

	def _set_voice(self, value: str):
		value = str(value)
		if value.startswith(_CUSTOM_PREFIX):
			name = value[len(_CUSTOM_PREFIX) :]
			custom = loadCustomVoices().get(name)
			if custom is not None:
				self._adoptCustomVoice(name, custom)
				self._enqueue(self._voiceBlock(), pitchRaised=False, supersedes=True)
				return
			# The voice was deleted since it was last selected; fall back rather than
			# leave NVDA pointing at something that no longer exists.
			value = str(self._voiceIndex)
		self._adoptVoice(_asIndex(value, len(_VOICES) - 1, self._voiceIndex))
		self._enqueue(self._voiceBlock(), pitchRaised=False, supersedes=True)
