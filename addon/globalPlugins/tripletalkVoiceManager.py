# Custom voice manager for the TripleTalk synthesizer driver.
#
# A custom voice is one of the eleven built-in voices plus the seven preset values
# the unit carries per voice. The driver owns the voice model and its storage; this
# module is only the user interface for it.

import globalPluginHandler
import gui
import synthDriverHandler
import wx
from gui import guiHelper
from logHandler import log

try:
	from synthDrivers import tripletalk
except Exception:
	tripletalk = None
	log.exception("TripleTalk voice manager: the driver could not be imported")

#: (field, label) for the parameters shown as spin controls, in the order the driver
#: presents the equivalent settings. Tone is a choice and is handled separately.
_SPIN_FIELDS = (
	("pitch", "&Pitch"),
	("inflection", "&Inflection"),
	("articulation", "&Articulation"),
	("formant", "&Formant frequency"),
	("reverb", "Re&verb"),
	("textDelay", "Text &delay"),
)
_TONE_NAMES = ("Bass", "Normal", "Treble")

_PREVIEW_TEXT = "The quick brown fox jumps over the lazy dog."

_TITLE = "TripleTalk Voice Manager"


def _activeSynth():
	"""The running TripleTalk driver, or None if another synthesizer is selected."""
	synth = synthDriverHandler.getSynth()
	if synth is None or synth.name != "tripletalk":
		return None
	return synth


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	def __init__(self):
		super().__init__()
		if tripletalk is None:
			return
		self._item = gui.mainFrame.sysTrayIcon.toolsMenu.Append(
			wx.ID_ANY,
			"TripleTalk &Voice Manager...",
			"Create and manage custom TripleTalk voices",
		)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self._onOpen, self._item)

	def terminate(self):
		if tripletalk is None:
			return
		try:
			gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self._item)
		except Exception:
			log.debugWarning("TripleTalk voice manager: could not remove its menu item")

	def _onOpen(self, event):
		gui.mainFrame.prePopup()
		try:
			ManagerDialog(gui.mainFrame).Show()
		finally:
			gui.mainFrame.postPopup()


class ManagerDialog(wx.Dialog):
	"""Lists saved voices and offers the actions that operate on one."""

	def __init__(self, parent):
		super().__init__(parent, title=_TITLE)
		main = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)

		self.voices = main.addLabeledControl(
			"Custom &voices:",
			wx.ListBox,
			size=(320, 200),
		)
		self.voices.Bind(wx.EVT_LISTBOX_DCLICK, self._onEdit)

		actions = guiHelper.BoxSizerHelper(self, orientation=wx.HORIZONTAL)
		self._buttons = {}
		for label, handler, needsSelection in (
			("&New...", self._onNew, False),
			("&Edit...", self._onEdit, True),
			("&Delete", self._onDelete, True),
			("E&xport...", self._onExport, True),
			("&Import...", self._onImport, False),
		):
			button = actions.addItem(wx.Button(self, label=label))
			button.Bind(wx.EVT_BUTTON, handler)
			if needsSelection:
				self._buttons[label] = button
		main.addItem(actions)

		main.addDialogDismissButtons(self.CreateButtonSizer(wx.CLOSE))
		self.Bind(wx.EVT_BUTTON, lambda e: self.Close(), id=wx.ID_CLOSE)
		# Needed despite the button above. Left to itself wx answers a close by clicking
		# the EscapeId button, whose handler calls Close() again, and wx's re-entry guard
		# swallows that one -- leaving button, X, Alt+F4 and Escape all doing nothing.
		self.Bind(wx.EVT_CLOSE, lambda e: self.Destroy())
		self.EscapeId = wx.ID_CLOSE

		self.SetSizerAndFit(main.sizer)
		self._reload()
		self.CentreOnScreen()

	def _reload(self, select=None):
		names = list(tripletalk.loadCustomVoices())
		self.voices.Set(names)
		if names:
			self.voices.SetSelection(names.index(select) if select in names else 0)
		for button in self._buttons.values():
			button.Enable(bool(names))
		self.voices.SetFocus()

	def _selected(self):
		index = self.voices.GetSelection()
		return self.voices.GetString(index) if index != wx.NOT_FOUND else None

	def _useVoice(self, name):
		"""Point NVDA at a saved voice, so a new one is heard straight away."""
		synth = _activeSynth()
		if synth is None:
			return
		try:
			synth.voice = tripletalk._CUSTOM_PREFIX + name
			synth.saveSettings()
		except Exception:
			log.exception("TripleTalk voice manager: could not select the voice")

	def _onNew(self, event):
		dialog = EditorDialog(self, existing=None)
		if dialog.ShowModal() == wx.ID_OK:
			self._reload(select=dialog.savedName)
			self._useVoice(dialog.savedName)
		dialog.Destroy()

	def _onEdit(self, event):
		name = self._selected()
		if name is None:
			return
		dialog = EditorDialog(self, existing=name)
		if dialog.ShowModal() == wx.ID_OK:
			self._reload(select=dialog.savedName)
			synth = _activeSynth()
			# Re-select it so an edit to the voice in use takes effect immediately,
			# and so a rename does not leave NVDA on an id that no longer exists.
			if synth is not None and synth.voice.startswith(tripletalk._CUSTOM_PREFIX):
				self._useVoice(dialog.savedName)
		dialog.Destroy()

	def _onDelete(self, event):
		name = self._selected()
		if name is None:
			return
		if gui.messageBox(
			f"Delete the custom voice {name}?",
			_TITLE,
			wx.YES_NO | wx.ICON_WARNING,
			self,
		) != wx.YES:
			return
		synth = _activeSynth()
		if synth is not None and synth.voice == tripletalk._CUSTOM_PREFIX + name:
			try:
				# Fall back to the built-in voice this one was based on.
				synth.voice = str(tripletalk.loadCustomVoices()[name].base)
				synth.saveSettings()
			except Exception:
				log.exception("TripleTalk voice manager: could not fall back after delete")
		tripletalk.deleteCustomVoice(name)
		self._reload()

	def _onExport(self, event):
		name = self._selected()
		if name is None:
			return
		with wx.FileDialog(
			self,
			"Export a TripleTalk voice",
			wildcard="TripleTalk voice (*.ttv)|*.ttv",
			defaultFile=f"{name}.ttv",
			style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		) as chooser:
			if chooser.ShowModal() != wx.ID_OK:
				return
			path = chooser.GetPath()
		try:
			tripletalk.exportCustomVoice(name, path)
		except (tripletalk.VoiceStoreError, OSError) as e:
			gui.messageBox(str(e), _TITLE, wx.OK | wx.ICON_ERROR, self)

	def _onImport(self, event):
		with wx.FileDialog(
			self,
			"Import a TripleTalk voice",
			wildcard="TripleTalk voice (*.ttv)|*.ttv",
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		) as chooser:
			if chooser.ShowModal() != wx.ID_OK:
				return
			path = chooser.GetPath()
		try:
			name = tripletalk.importCustomVoice(path)
		except tripletalk.VoiceStoreError as e:
			gui.messageBox(str(e), _TITLE, wx.OK | wx.ICON_ERROR, self)
			return
		self._reload(select=name)


class EditorDialog(wx.Dialog):
	"""Edits one voice: its name, the built-in it starts from, and the seven values.

	A new voice starts from whatever the synthesizer is saying now, so a voice can be
	tuned with NVDA's own controls and then simply named here. Choosing a different
	base resets the values to that voice's own, making it a clean starting point.
	"""

	def __init__(self, parent, existing=None):
		self.existing = existing
		self.savedName = None
		super().__init__(parent, title="Edit voice" if existing else "New voice")

		base, values = self._startingPoint(existing)

		main = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
		self.nameCtrl = main.addLabeledControl("&Name:", wx.TextCtrl, value=existing or "")
		self.baseCtrl = main.addLabeledControl(
			"&Based on:",
			wx.Choice,
			choices=[voice.name for voice in tripletalk._VOICES],
		)
		self.baseCtrl.SetSelection(base)
		self.baseCtrl.Bind(wx.EVT_CHOICE, self._onBaseChanged)

		self.spins = {}
		for field, label in _SPIN_FIELDS:
			maximum = tripletalk._VOICE_FIELDS[field]
			spin = main.addLabeledControl(
				f"{label} (0 to {maximum}):",
				wx.SpinCtrl,
				min=0,
				max=maximum,
				initial=values[field],
			)
			self.spins[field] = spin

		self.toneCtrl = main.addLabeledControl("&Tone:", wx.Choice, choices=list(_TONE_NAMES))
		self.toneCtrl.SetSelection(values["tone"])

		self.testButton = main.addItem(wx.Button(self, label="&Test"))
		self.testButton.Bind(wx.EVT_BUTTON, self._onTest)

		main.addDialogDismissButtons(self.CreateButtonSizer(wx.OK | wx.CANCEL))
		self.Bind(wx.EVT_BUTTON, self._onOk, id=wx.ID_OK)
		# Alt+T reaches Test from anywhere, including from inside a spin control.
		self.SetAcceleratorTable(
			wx.AcceleratorTable([(wx.ACCEL_ALT, ord("T"), self.testButton.GetId())]),
		)

		self.SetSizerAndFit(main.sizer)
		self.CentreOnScreen()
		self.nameCtrl.SetFocus()

	def _startingPoint(self, existing):
		"""(base index, values) to open with."""
		if existing is not None:
			voice = tripletalk.loadCustomVoices().get(existing)
			if voice is not None:
				return voice.base, dict(voice.params)
		synth = _activeSynth()
		if synth is not None:
			base, params = synth.currentVoiceDefinition()
			return base, dict(params)
		builtIn = tripletalk._VOICES[tripletalk._DEFAULT_VOICE]
		return tripletalk._DEFAULT_VOICE, {
			field: getattr(builtIn, field) for field in tripletalk._VOICE_FIELDS
		}

	def _params(self):
		params = {field: spin.GetValue() for field, spin in self.spins.items()}
		params["tone"] = self.toneCtrl.GetSelection()
		return params

	def _onBaseChanged(self, event):
		voice = tripletalk._VOICES[self.baseCtrl.GetSelection()]
		for field, spin in self.spins.items():
			spin.SetValue(getattr(voice, field))
		self.toneCtrl.SetSelection(voice.tone)

	def _onTest(self, event):
		synth = _activeSynth()
		if synth is None:
			gui.messageBox(
				"Set NVDA's synthesizer to TripleTalk to hear a voice.",
				_TITLE,
				wx.OK | wx.ICON_INFORMATION,
				self,
			)
			return
		try:
			synth.previewVoice(self.baseCtrl.GetSelection(), self._params(), _PREVIEW_TEXT)
		except Exception:
			log.exception("TripleTalk voice manager: the preview failed")

	def _onOk(self, event):
		name = self.nameCtrl.GetValue().strip()
		if not name:
			gui.messageBox("The voice needs a name.", _TITLE, wx.OK | wx.ICON_ERROR, self)
			self.nameCtrl.SetFocus()
			return
		if name != self.existing and name in tripletalk.loadCustomVoices():
			if gui.messageBox(
				f"There is already a voice named {name}. Replace it?",
				_TITLE,
				wx.YES_NO | wx.ICON_WARNING,
				self,
			) != wx.YES:
				return
		try:
			tripletalk.saveCustomVoice(name, self.baseCtrl.GetSelection(), self._params())
		except tripletalk.VoiceStoreError as e:
			gui.messageBox(str(e), _TITLE, wx.OK | wx.ICON_ERROR, self)
			return
		if self.existing is not None and self.existing != name:
			tripletalk.deleteCustomVoice(self.existing)
		self.savedName = name
		self.EndModal(wx.ID_OK)
