# Voice Parameter Index

This page documents the various voice parameters used by the TripleTalk line of hardware speech synthesizers. These parameters are used by the NVDA add-on, but could also be useful for other text-to-speech packages.

> Please note that these values have been calibrated by ear, but they have been tested to be correct based on hands-on experimentation by myself.

## Pitch

This command varies the pitch over a wide range, which can be used to change the average pitch during speech production, produce manual intonation, or create sound effects (including singing). Pitch values can range from 0P through 99P; the default is 50P.

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

Expression, or intonation, is the variation of pitch within a sentence or phrase. When expression is enabled (n>0), TripleTalk attempts to mimic the pitch patterns of human speech. For example, when a sentence ends with a period, the pitch drops at the end of the sentence; a question mark will cause the pitch to rise. The optional parameter n determines the degree of intonation. 0E provides no intonation (monotone), whereas 9E is very animated sounding. 5E is the default setting.

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

This command adjusts the articulation level, from 0A through 9A. Excessively low articulation values tend to make the voice sound slurred; very high values, on the other hand, can make the voice sound choppy. The default articulation is 5A.

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

This command adjusts the synthesizer's overall frequency response (vocal tract formant frequencies), over the range 0F through 99F. By varying the frequency, voice quality can be fine-tuned or voice type changed. The default frequency is 50F.

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

This command is used to add reverberation to the voice. 0R (the default) introduces no reverb; increasing values of n correspondingly increase the reverb delay and effect. 9R is the maximum setting.

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
