# TripleTalk USB / TripleTalk USB Mini for NVDA
This is a driver for the TripleTalk USB Mini speech synthesizer from Access Solutions.
This driver also works with the original TripleTalk USB, but keep in mind that the driver was tested against the Mini, so I cannot guarantee that it works with the TripleTalk USB in USB mode.
> Note that this add-on has been vibe coded with the help of AI, so not everything might be perfect. There might be some crashes here and there, and the code might not be up to par. Human contributions and feedback is always welcome.
## Features
This add-on features voice support, gaining access to all eleven TripleTalk USB voices via the voice selection dialogue.
The eight standard voices are available, including:
- Perfect Paul
- Vader
- Big Bob
- Precise Pete
- Ricochet Randy
- Biff
- Skip
- Robo Robert
The TripleTalk USB Mini, being based on the RC8660 chip, includes three additional voices:
- Goliath
- Alvin
- Gretchen
All these voices were taken from the official RC8660 developer manual, including the proper names for Big Bob and Ricochet Randy, which have been renamed in the official TripleTalk USB developer manual, but I decided to use the official names.
All extra TripleTalk parameters are accessible via NVDA's speech settings, including the following extra parameters:
- Formant (range 0 to 99 on the Mini)
- Articulation (range 0 to 9)
- Reverb/Delay (range 0 to 9)
- Text Delay (range 0 to 15)
- Tone (with three values; 0 = Bass, 1 = Normal, and 2 = Treble)
## Requirements
This driver has been tested with NVDA 2025.3, but also works with NVDA 2025.1. I cannot guarantee that it works with NVDA 2026.1 (which is set to be released soon).
In order to use this add-on, you will need the TripleTalk USB Mini drivers to be installed and working. You can get them from [this mirror](https://dectalk.nu/Software%20and%20Manuals/Hardware/TripleTalk/Software/ttum-disk101.zip)
If you're on a 64-bit Windows install, you will need the [signed 64-bit drivers](https://dectalk.nu/Software%20and%20Manuals/Hardware/TripleTalk/Drivers/ttusb64.zip), as the 64-bit drivers included above do not work.
## What's Left to Do?
Despite the driver working as expected in casual use, there are some things that still need to be worked on.
- Because of how pitch changes are implemented, the driver does not honor the capital pitch changes in NVDA's voice settings dialog.
- Add a proper combo box for Tone.
- Add Spanish language support.
