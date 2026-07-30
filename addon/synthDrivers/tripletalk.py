# NVDA synth driver for the Access Solutions TripleTalk USB
# and TripleTalk USB Mini hardware speech synthesizers.
#
import ctypes
import ctypes.wintypes
import os
import queue
import struct
import threading
from collections import OrderedDict

from autoSettingsUtils.driverSetting import DriverSetting, NumericDriverSetting
from autoSettingsUtils.utils import StringParameterInfo
from logHandler import log
from speech.commands import IndexCommand, PitchCommand
from synthDriverHandler import (
	SynthDriver,
	VoiceInfo,
	synthDoneSpeaking,
	synthIndexReached,
)


# --- Wire protocol constants -------------------------------------------------

CMD = 0x01           # Ctrl-A: command introducer
STOP = 0x18          # Ctrl-X: stop speech and flush input buffer
NUL = 0x00           # Forces the chip to translate buffered text



def _cmdSet(letter: str, value: int) -> bytes:
	"""Build a chip command: Ctrl-A <ASCII digits of value> <letter>."""
	return bytes([CMD]) + str(value).encode("ascii") + letter.encode("ascii")


def _cmdRelPitch(delta: int) -> bytes:
	"""Build a relative pitch command: Ctrl-A <+/-><digits>P.

	The chip saturates relative parameters at the 0..99 range, so we don't
	need to clamp on our side — but we do need to emit a literal sign.
	"""
	sign = "+" if delta >= 0 else "-"
	return bytes([CMD]) + f"{sign}{abs(delta)}".encode("ascii") + b"P"


_WINDIR = os.environ.get("WINDIR", r"C:\Windows")
if struct.calcsize("P") == 8:
	_DLL_PATH = os.path.join(_WINDIR, "System32", "ttusbd64.dll")
else:
	_DLL_PATH = os.path.join(_WINDIR, "ttusbd.dll")

# TripleTalk USB VID/PID — shared by both the original and Mini models.
_TT_VID = 0x0DD0
_TT_PID = 0x1002

# GUID_DEVINTERFACE_USB_DEVICE
_GUID_USB = ctypes.c_byte * 16


class _SP_DEVINFO_DATA(ctypes.Structure):
	_fields_ = [
		("cbSize", ctypes.wintypes.DWORD),
		("ClassGuid", ctypes.c_byte * 16),
		("DevInst", ctypes.wintypes.DWORD),
		("Reserved", ctypes.POINTER(ctypes.c_ulong)),
	]


def _isDevicePresent() -> bool:
	"""Check if a TripleTalk USB device is currently connected using SetupAPI.

	This avoids the DLL entirely, so it responds correctly to hot-plug
	without stale cached state.
	"""
	try:
		setupapi = ctypes.WinDLL("setupapi")
	except Exception:
		return False

	DIGCF_PRESENT = 0x02
	DIGCF_ALLCLASSES = 0x04

	setupapi.SetupDiGetClassDevsW.restype = ctypes.wintypes.HANDLE
	setupapi.SetupDiGetClassDevsW.argtypes = [
		ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.wintypes.DWORD,
	]
	setupapi.SetupDiEnumDeviceInfo.restype = ctypes.wintypes.BOOL
	setupapi.SetupDiEnumDeviceInfo.argtypes = [
		ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD, ctypes.POINTER(_SP_DEVINFO_DATA),
	]
	setupapi.SetupDiGetDeviceRegistryPropertyW.restype = ctypes.wintypes.BOOL
	setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
		ctypes.wintypes.HANDLE, ctypes.POINTER(_SP_DEVINFO_DATA),
		ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.DWORD),
		ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.DWORD),
	]
	setupapi.SetupDiDestroyDeviceInfoList.restype = ctypes.wintypes.BOOL
	setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.wintypes.HANDLE]

	hDevInfo = setupapi.SetupDiGetClassDevsW(
		None, "USB", None, DIGCF_PRESENT | DIGCF_ALLCLASSES,
	)
	INVALID_HANDLE = ctypes.wintypes.HANDLE(-1).value
	if hDevInfo is None or hDevInfo == INVALID_HANDLE:
		return False

	devInfoData = _SP_DEVINFO_DATA()
	devInfoData.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)

	target = f"VID_{_TT_VID:04X}&PID_{_TT_PID:04X}".upper()
	found = False
	index = 0

	try:
		while setupapi.SetupDiEnumDeviceInfo(hDevInfo, index, ctypes.byref(devInfoData)):
			buf = ctypes.create_unicode_buffer(512)
			SPDRP_HARDWAREID = 1
			if setupapi.SetupDiGetDeviceRegistryPropertyW(
				hDevInfo, ctypes.byref(devInfoData),
				SPDRP_HARDWAREID, None, ctypes.cast(buf, ctypes.c_void_p),
				ctypes.sizeof(buf), None,
			):
				hwid = buf.value.upper()
				if target in hwid:
					found = True
					break
			index += 1
	finally:
		setupapi.SetupDiDestroyDeviceInfoList(hDevInfo)

	return found


class _TTUSBDLL:
	def __init__(self):
		self._dll = ctypes.CDLL(_DLL_PATH)
		self._dll.USBTT_CheckWdmStatus.restype = ctypes.c_int
		self._dll.USBTT_CheckWdmStatus.argtypes = []
		self._dll.USBTT_WriteByte.restype = None
		self._dll.USBTT_WriteByte.argtypes = [ctypes.c_int]
		self._dll.USBTT_WriteByteImmediate.restype = None
		self._dll.USBTT_WriteByteImmediate.argtypes = [ctypes.c_int]
		self._dll.USBTT_ReadyCheck.restype = ctypes.c_int
		self._dll.USBTT_ReadyCheck.argtypes = []
		self._dll.USBTT_ReadByte.restype = ctypes.c_int
		self._dll.USBTT_ReadByte.argtypes = []

	def checkWdmStatus(self) -> int:
		return self._dll.USBTT_CheckWdmStatus()

	def writeByte(self, value: int) -> None:
		self._dll.USBTT_WriteByte(value)

	def writeByteImmediate(self, value: int) -> None:
		self._dll.USBTT_WriteByteImmediate(value)

	def readByte(self) -> int:
		return self._dll.USBTT_ReadByte()


class SynthDriver(SynthDriver):
	name = "tripletalk"
	description = "TripleTalk USB"

	supportedSettings = (
		SynthDriver.VoiceSetting(),
		SynthDriver.RateSetting(minStep=10),
		SynthDriver.PitchSetting(),
		SynthDriver.InflectionSetting(minStep=10),
		SynthDriver.VolumeSetting(minStep=10),
		NumericDriverSetting(
			"articulation",
			"&Articulation",
			availableInSettingsRing=False,
			defaultVal=50, minVal=0, maxVal=100, minStep=10,
		),
		NumericDriverSetting(
			"formant",
			"&Formant frequency",
			availableInSettingsRing=False,
			defaultVal=50, minVal=0, maxVal=100, minStep=1,
		),
		NumericDriverSetting(
			"reverb",
			"Re&verb",
			availableInSettingsRing=False,
			defaultVal=0, minVal=0, maxVal=100, minStep=10,
		),
		DriverSetting(
			"textDelay",
			"Text &delay",
			availableInSettingsRing=False,
			defaultVal="0",
		),
		DriverSetting(
			"tone",
			"&Tone",
			availableInSettingsRing=False,
			defaultVal="1",
		),
	)
	supportedCommands = frozenset({IndexCommand, PitchCommand})
	supportedNotifications = frozenset({synthIndexReached, synthDoneSpeaking})

	_availableVoices = OrderedDict([
		("0", VoiceInfo("0", "Perfect Paul")),
		("1", VoiceInfo("1", "Vader")),
		("2", VoiceInfo("2", "Big Bob")),
		("3", VoiceInfo("3", "Precise Pete")),
		("4", VoiceInfo("4", "Ricochet Randy")),
		("5", VoiceInfo("5", "Biff")),
		("6", VoiceInfo("6", "Skip")),
		("7", VoiceInfo("7", "Robo Robert")),
		("8", VoiceInfo("8", "Goliath")),
		("9", VoiceInfo("9", "Alvin")),
		("10", VoiceInfo("10", "Gretchen")),
	])

	_VOICE_DEFAULTS = {
		# id: pitch, inflection, articulation, formant, reverb, textDelay, tone
		0:  {"pitch": 50, "inflection": 5, "articulation": 5, "formant": 50, "reverb": 0, "textDelay": 0, "tone": 1},  # Perfect Paul
		1:  {"pitch": 10, "inflection": 6, "articulation": 4, "formant": 40, "reverb": 3, "textDelay": 0, "tone": 1},  # Vader
		2:  {"pitch": 40, "inflection": 5, "articulation": 5, "formant": 46, "reverb": 0, "textDelay": 0, "tone": 0},  # Big Bob
		3:  {"pitch": 60, "inflection": 4, "articulation": 6, "formant": 52, "reverb": 0, "textDelay": 0, "tone": 2},  # Precise Pete
		4:  {"pitch": 40, "inflection": 5, "articulation": 5, "formant": 50, "reverb": 9, "textDelay": 0, "tone": 1},  # Ricochet Randy
		5:  {"pitch": 50, "inflection": 7, "articulation": 6, "formant": 40, "reverb": 0, "textDelay": 0, "tone": 0},  # Biff
		6:  {"pitch": 15, "inflection": 7, "articulation": 6, "formant": 59, "reverb": 0, "textDelay": 0, "tone": 0},  # Skip
		7:  {"pitch": 80, "inflection": 0, "articulation": 5, "formant": 54, "reverb": 6, "textDelay": 1, "tone": 1},  # Robo Robert
		8:  {"pitch": 20, "inflection": 5, "articulation": 5, "formant": 15, "reverb": 2, "textDelay": 0, "tone": 1},  # Goliath
		9:  {"pitch": 60, "inflection": 5, "articulation": 5, "formant": 98, "reverb": 0, "textDelay": 0, "tone": 2},  # Alvin
		10: {"pitch": 99, "inflection": 5, "articulation": 5, "formant": 67, "reverb": 0, "textDelay": 0, "tone": 2},  # Gretchen
	}

	@classmethod
	def check(cls):
		if not _isDevicePresent():
			return False
		if not os.path.isfile(_DLL_PATH):
			log.debug(f"TripleTalk: DLL not found at {_DLL_PATH}")
			return False
		return True

	def __init__(self):
		super().__init__()

		self._dll = _TTUSBDLL()
		self._dll.checkWdmStatus()

		self._chipVoice = 0
		self._chipRate = 3
		self._chipVolume = 5
		_d = self._VOICE_DEFAULTS[self._chipVoice]
		self._chipPitch = _d["pitch"]
		self._chipInflection = _d["inflection"]
		self._chipArticulation = _d["articulation"]
		self._chipFormant = _d["formant"]
		self._chipReverb = _d["reverb"]
		self._chipTextDelay = _d["textDelay"]
		self._chipTone = _d["tone"]

		# Threading.
		self._writeQueue: "queue.Queue" = queue.Queue()
		self._cancelEvent = threading.Event()
		self._stopping = threading.Event()

		self._writerThread = threading.Thread(
			target=self._writerLoop, name="TripleTalk-Writer", daemon=True,
		)
		self._writerThread.start()

		# Initial chip state: enter Text mode (with current text-delay), set
		# the voice, set the chip's default punctuation filter (NVDA does its
		# own symbol processing on top), and push every cached prosody value.
		# Voice MUST come before the per-voice parameters because nO loads
		# the voice's intrinsics and would clobber anything set earlier.
		self._enqueue(
			_cmdSet("T", self._chipTextDelay)
			+ _cmdSet("O", self._chipVoice)
			+ _cmdSet("B", 6)
			+ _cmdSet("S", self._chipRate)
			+ _cmdSet("P", self._chipPitch)
			+ _cmdSet("E", self._chipInflection)
			+ _cmdSet("V", self._chipVolume)
			+ _cmdSet("A", self._chipArticulation)
			+ _cmdSet("F", self._chipFormant)
			+ _cmdSet("R", self._chipReverb)
			+ _cmdSet("X", self._chipTone)
			+ bytes([NUL])
		)

	# --- Lifecycle -----------------------------------------------------------

	def terminate(self):
		try:
			self._dll.writeByteImmediate(STOP)
		except Exception:
			pass
		self._stopping.set()
		self._cancelEvent.set()
		# Unblock the writer thread waiting on the queue.
		self._writeQueue.put(None)
		self._writerThread.join(timeout=2)
		# Force-unload the DLL so the next __init__ gets a fresh connection
		# to ttusb.sys. Without this, a hot-plug cycle (unplug + replug)
		# leaves the DLL's internal handle stale and CheckWdmStatus returns 0.
		try:
			ctypes.windll.kernel32.FreeLibrary(self._dll._dll._handle)
		except Exception:
			pass

	# --- Speech API ----------------------------------------------------------

	def speak(self, speechSequence):
		out = bytearray()
		effectiveChip = self._chipPitch
		for item in speechSequence:
			if isinstance(item, str):
				out += self._sanitizeText(item).encode("latin-1", errors="replace")
			elif isinstance(item, IndexCommand):
				# Fire the callback immediately so NVDA's SpeechManager
				# knows this index was reached and can deliver the next
				# speech chunk without waiting for the chip to catch up.
				synthIndexReached.notify(synth=self, index=item.index)
			elif isinstance(item, PitchCommand):
				targetChip = max(0, min(99, self._chipPitch + item.offset))
				delta = targetChip - effectiveChip
				if delta != 0:
					out += _cmdRelPitch(delta)
					effectiveChip = targetChip
		if effectiveChip != self._chipPitch:
			out += _cmdSet("P", self._chipPitch)
		out.append(NUL)
		self._enqueue(bytes(out))
		synthDoneSpeaking.notify(synth=self)

	def cancel(self):
		self._cancelEvent.set()
		try:
			while True:
				self._writeQueue.get_nowait()
		except queue.Empty:
			pass
		try:
			self._dll.writeByteImmediate(STOP)
		except Exception as e:
			log.error(f"TripleTalk: cancel WriteByteImmediate failed: {e}")

	# --- Settings ------------------------------------------------------------

	def _get_rate(self):
		return self._chipRate * 10

	def _set_rate(self, value):
		chipVal = max(0, min(9, value // 10))
		self._chipRate = chipVal
		self._enqueue(_cmdSet("S", chipVal) + bytes([NUL]))

	def _get_pitch(self):
		return self._chipPitch

	def _set_pitch(self, value):
		chipVal = max(0, min(99, value))
		self._chipPitch = chipVal
		self._enqueue(_cmdSet("P", chipVal) + bytes([NUL]))

	def _get_inflection(self):
		return self._chipInflection * 10

	def _set_inflection(self, value):
		chipVal = max(0, min(9, value // 10))
		self._chipInflection = chipVal
		self._enqueue(_cmdSet("E", chipVal) + bytes([NUL]))

	def _get_volume(self):
		return self._chipVolume * 10

	def _set_volume(self, value):
		chipVal = max(0, min(9, value // 10))
		self._chipVolume = chipVal
		self._enqueue(_cmdSet("V", chipVal) + bytes([NUL]))

	def _get_articulation(self):
		return self._chipArticulation * 10

	def _set_articulation(self, value):
		chipVal = max(0, min(9, value // 10))
		self._chipArticulation = chipVal
		self._enqueue(_cmdSet("A", chipVal) + bytes([NUL]))

	def _get_formant(self):
		return self._chipFormant

	def _set_formant(self, value):
		chipVal = max(0, min(99, value))
		self._chipFormant = chipVal
		self._enqueue(_cmdSet("F", chipVal) + bytes([NUL]))

	def _get_reverb(self):
		return self._chipReverb * 10

	def _set_reverb(self, value):
		chipVal = max(0, min(9, value // 10))
		self._chipReverb = chipVal
		self._enqueue(_cmdSet("R", chipVal) + bytes([NUL]))

	def _get_availableTextdelays(self):
		return OrderedDict(
			(str(i), StringParameterInfo(str(i), str(i)))
			for i in range(16)
		)

	def _get_textDelay(self):
		return str(self._chipTextDelay)

	def _set_textDelay(self, value):
		try:
			chipVal = int(value)
		except (TypeError, ValueError):
			return
		if chipVal not in range(16):
			return
		self._chipTextDelay = chipVal
		self._enqueue(_cmdSet("T", chipVal) + bytes([NUL]))

	def _get_availableTones(self):
		return OrderedDict([
			("0", StringParameterInfo("0", "Bass")),
			("1", StringParameterInfo("1", "Normal")),
			("2", StringParameterInfo("2", "Treble")),
		])

	def _get_tone(self):
		return str(self._chipTone)

	def _set_tone(self, value):
		try:
			chipVal = int(value)
		except (TypeError, ValueError):
			return
		if chipVal not in range(3):
			return
		self._chipTone = chipVal
		self._enqueue(_cmdSet("X", chipVal) + bytes([NUL]))

	def _get_voice(self):
		return str(self._chipVoice)

	def _set_voice(self, value):
		try:
			chipVal = int(value)
		except (TypeError, ValueError):
			return
		if chipVal not in range(11):
			return
		self._chipVoice = chipVal
		d = self._VOICE_DEFAULTS[chipVal]
		self._chipPitch = d["pitch"]
		self._chipInflection = d["inflection"]
		self._chipArticulation = d["articulation"]
		self._chipFormant = d["formant"]
		self._chipReverb = d["reverb"]
		self._chipTextDelay = d["textDelay"]
		self._chipTone = d["tone"]
		self._enqueue(_cmdSet("O", chipVal) + bytes([NUL]))

	# --- Internals -----------------------------------------------------------

	@staticmethod
	def _sanitizeText(text: str) -> str:
		# Replace control bytes the chip would interpret as commands
		# (Ctrl-A, Ctrl-X, Ctrl-Y, Ctrl-^, etc.) with spaces. Keep ordinary
		# whitespace.
		out = []
		for ch in text:
			o = ord(ch)
			if o < 0x20 and ch not in ("\t", "\n", "\r"):
				out.append(" ")
			else:
				out.append(ch)
		return "".join(out)

	def _enqueue(self, data: bytes) -> None:
		self._writeQueue.put(data)

	def _writerLoop(self):
		while not self._stopping.is_set():
			item = self._writeQueue.get()
			if item is None:
				break
			# Fresh utterance: clear any leftover cancel state.
			self._cancelEvent.clear()
			cancelled = False
			for byte in item:
				if self._cancelEvent.is_set():
					cancelled = True
					break
				try:
					self._dll.writeByte(byte)
				except Exception as e:
					log.error(f"TripleTalk: WriteByte failed: {e}")
					cancelled = True
					break
			pass
