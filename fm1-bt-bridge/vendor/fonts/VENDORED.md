# Vendored: the Terminus pixel font

`ter-u16b.bdf`, `ter-u16n.bdf` and `Terminus-LICENSE.txt` are copied unchanged from
[isod89/sloop-fm1](https://github.com/isod89/sloop-fm1) (`assets/fonts/`, commit
`d89364253b79a5c6002c0d90c5f16b3c3c16ea9d`), the font SLOOP draws its screens with, so the desk box's
touchscreen looks like the FM-1 running SLOOP.

Terminus is Copyright (C) 2020 Dimitar Toshkov Zhekov, under the SIL Open Font License 1.1 (see
`Terminus-LICENSE.txt`). `sloopstyle.py` turns the BDF into Pillow's own font format the first time it's needed
(kept in `~/.cache/fm1-bridge/`).
