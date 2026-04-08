# Voice Parameter Index

This page documents the various voice parameters used by the TripleTalk line of hardware speech synthesizers. These parameters are used by the NVDA add-on, but could also be useful for other text-to-speech packages.

> Please note that these values have been calibrated by ear, but they have been tested to be correct based on hands-on experimentation by myself.

## Pitch

Sets the synthesizer's baseline pitch. The speed is not affected. The RC8660's intonation algorithms will vary the pitch above and below the base pitch by an amount dependent upon the Inflection setting. The default is 50, with 0 and 99 being the accepted values, any other value outside that range will cause a wraparound.

| Voice | Value |
| --- | --- |
| Perfect Paul | 50 |
| Vader | 10 |
| Big Bob | 40 |
| Precise Pete | 60 |
| Ricochet Randy | 40 |
| Biff | 50 |
| Skip | 15 |
| Robo Robert | 80 |
| Goliath | 20 |
| Alvin | 60 |
| Gretchen | 99 |

## Inflection

Inflection, sometimes called expression or intonation, is the variation of pitch within a sentence or phrase. Determines how much intonation, if any, is introduced into the voice. Low values lean towards being monotonic, while large values become "sing-songy." The intonation contour is influenced by sentence structure, particularly punctuation symbols. 0 provides no intonation), whereas 9 is very animated sounding. 5 is the default setting.

| Voice | Value |
| --- | --- |
| Perfect Paul | 5 |
| Vader | 6 |
| Big Bob | 5 |
| Precise Pete | 4 |
| Ricochet Randy | 5 |
| Biff | 7 |
| Skip | 7 |
| Robo Robert | 0 |
| Goliath | 5 |
| Alvin | 5 |
| Gretchen | 5 |

## Articulation

Determines the intensity of certain unvoiced sounds. Setting this control too low can make the speech sound slurred. 5 is the default, while 0 and 9 are the accepted values.

| Voice | Value |
| --- | --- |
| Perfect Paul | 5 |
| Vader | 4 |
| Big Bob | 5 |
| Precise Pete | 6 |
| Ricochet Randy | 5 |
| Biff | 6 |
| Skip | 6 |
| Robo Robert | 5 |
| Goliath | 5 |
| Alvin | 5 |
| Gretchen | 5 |

## Formant Frequency

Varies the TTS synthesizer's internal sampling rate — the effect is similar to varying the speed of a record or tape player (both pitch and speed are affected). Default value is 50.

| Voice | Value |
| --- | --- |
| Perfect Paul | 50 |
| Vader | 40 |
| Big Bob | 46 |
| Precise Pete | 52 |
| Ricochet Randy | 50 |
| Biff | 40 |
| Skip | 59 |
| Robo Robert | 54 |
| Goliath | 15 |
| Alvin | 98 |
| Gretchen | 67 |

## Reverb

This command is used to add reverberation to the voice. 0 (the default) introduces no reverb; increasing values increase the reverb delay and effect. 9 is the maximum setting.

| Voice | Value |
| --- | --- |
| Perfect Paul | 0 |
| Vader | 3 |
| Big Bob | 0 |
| Precise Pete | 0 |
| Ricochet Randy | 9 |
| Biff | 0 |
| Skip | 0 |
| Robo Robert | 6 |
| Goliath | 2 |
| Alvin | 0 |
| Gretchen | 0 |

## Text Delay

The optional delay parameter n is used to create a variable pause between words. The shortest, and default delay of 0, is used for normal speech. For users not accustomed to synthetic speech, the synthesizer's intelligibility may be improved by introducing a delay. The longest delay that can be specified is 15.

| Voice | Value |
| --- | --- |
| Perfect Paul | 0 |
| Vader | 0 |
| Big Bob | 0 |
| Precise Pete | 0 |
| Ricochet Randy | 0 |
| Biff | 0 |
| Skip | 0 |
| Robo Robert | 1 |
| Goliath | 0 |
| Alvin | 0 |
| Gretchen | 0 |

## Tone

The synthesizer supports three tone settings, bass (0X), normal (1X) and treble (2X). The best setting to use depends on the speaker being used and personal preference. Normal (1X) is the default setting.

| Voice | Value |
| --- | --- |
| Perfect Paul | Normal |
| Vader | Normal |
| Big Bob | Bass |
| Precise Pete | Treble |
| Ricochet Randy | Normal |
| Biff | Bass |
| Skip | Bass |
| Robo Robert | Normal |
| Goliath | Normal |
| Alvin | Treble |
| Gretchen | Treble |
