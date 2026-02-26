# TripleTalk USB / TripleTalk USB Mini for NVDA
This is a driver for the [TripleTalk USB Mini](https://www.blindmicemegamall.com/bmm/shop/Item_Detail?itemid=5323293) hardware speech synthesizer from Access Solutions.

This add-on also works with the original TripleTalk USB since they use the same Windows drivers, but keep in mind that the NVDA add-on was tested against the Mini, so I cannot guarantee that it works with the TripleTalk USB in USB mode, as I don't have access to one myself.

> Please Note: This add-on has been vibe coded with the help of AI, so not everything might be perfect. There might be some crashes here and there, and the code might not be up to par, and some of the implementations might be hacky at best. Despite this, however, actual human verification was done by myself and another tester who has an actual TripleTalk unit to verify everything works as intended. Real human contributions, fixes, and feedback are always welcome.

> Also worth noting is that this isn't a Python 3 port of the existing TripleTalk USB add-on from Alex H. This add-on was made from scratch, but credit goes to him for creating the original Python 2 driver.

## Download
You can download the add-on directly via the [releases](https://github.com/retroBunn/tTUSB-NVDA/releases/) tab.

If you're subscribed to the [current DECtalk Archive mirror])https://dectalk.nu/) through Resilio Sync, or if you're browsing the directory, you can also download the add-on [over here](https://dectalk.nu/Software%20and%20Manuals/Software/NVDA%20Add-ons/NVDA%202019.3%20and%20Beyond/tripletalk.nvda-addon).

## Features
This add-on features voice support, gaining access to all eleven TripleTalk USB voices via the voice selection dialog.

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

All these voices were taken from the official RC8660 developer manual, including the proper names for Big Bob and Ricochet Randy, which have been renamed in the official TripleTalk USB developer manual to Mountain Mike and Jammin Jimmy respectively, but I decided to use the official names.

All extra TripleTalk parameters are accessible via NVDA's speech settings, including the following extra parameters:
- Formant (range 0 to 99 on the Mini)
- Articulation (range 0 to 9)
- Reverb/Echo (range 0 to 9)
- Text Delay (range 0 to 15)
- Tone (with three values; Bass, Normal, and Treble)

## Requirements
This driver has been tested with NVDA 2025.3. I cannot guarantee that it works with NVDA 2026.1 (which is set to be released soon, and is to be 64-bit only). Rest assured that I'll be trying my best to get it working as soon as I can.

In order to use this add-on, you will need the TripleTalk USB Mini drivers installed and working. You can get them from [this mirror](https://dectalk.nu/Software%20and%20Manuals/Hardware/TripleTalk/Software/ttum-disk101.zip).

If you're on a 64-bit Windows install, you will need the [signed 64-bit drivers](https://dectalk.nu/Software%20and%20Manuals/Hardware/TripleTalk/Drivers/ttusb64.zip), as the 64-bit drivers included above do not work. Be sure to follow the instructions in the installation manual to get your TripleTalk up and running.

To check that your unit works after the Windows drivers are installed, on the TripleTalk USB Mini installation disk, go into the `utils` directory and launch `TTUAPP.EXE` or `ttuapp64.exe`. This is a utility that will allow you to send text to the TripleTalk units through the **Send Text** or **Send File** commands in the file menu.

## Current Limitations
Despite the driver working as expected in casual use, there are some limitations that I don't think can easily be fixed.

- New line pauses: When NVDA encounters a new line, for example in the File Explorer, normally it's supposed to pause in between new lines (such as the case with "This PC File Explorer, Items View List". But due to limitations with the RC8660 chip, there's no way of having it pause when it encounters new lines sent to the synthesizer. This same behavior happens when the synthesizer is being used with other screen readers such as JAWS.
- Spanish support: The TripleTalk USB can speak Spanish, but it has to load a Spanish dictionary file. Spanish isn't planned at the moment, due to how it handles custom dictionary loading at the moment, unless someone can figure out how to get it working.