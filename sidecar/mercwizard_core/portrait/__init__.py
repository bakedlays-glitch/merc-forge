"""Portrait pipeline: image input → 4 STI files written to the game.

Modules:
- quantize: 255-color palette with anchor + rawmode fix + black scrub
- animate_skip: 7 dummy animation sub-frames (the 8-frame STI guarantee)
- animate_explicit: 7 sub-frames from user-supplied eye/mouth PNGs
- sizes: aspect-aware center crop and scaling to (48,43), (31,27), (15,14), (106,122)
- sti: ETRLE encoding via ja2py + frame-count assertions
"""
