# Files from SLOOP

These files are copied unchanged from [SLOOP](https://github.com/isod89/sloop-fm1) (isod89, based on
[Felucca](https://github.com/hugelton/Felucca) by Leo Kuroshita / Hügelton Instruments):

| File | SLOOP path | What the bridge uses it for |
|---|---|---|
| `fm1_install.py` | `tools/fm1_install.py` | Flashing a firmware package (`.fwsc`) onto the FM-1 over USB MIDI |
| `sampleio.py` | `tools/sampleio.py` | Turning WAV files into a SLOOP user sample slot (IMA ADPCM) |

- **Version:** SLOOP 2.8, commit `b77bf55e3bf804665b8f5dffb5955bd8443311f0`.
- **SHA-256:** `fm1_install.py` `9e117608e328b91dd5cb178400d9c88b1ee45677b87ae8b704536c0f38fea8f9`,
  `sampleio.py` `b4500741f30e42ee4759abc064ee2ea4b365099e53d47a338e96819a86457ec1`.
- **Licence:** GPL-3.0-only, Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments (see each
  file's header and SLOOP's `LICENSING.md`). This repository is GPL-3.0 as well.

Keep them unchanged so they can be updated by copying newer versions over them; the bridge's own code
(`sloop.py`, `library.py`) wraps them.
