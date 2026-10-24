"""The Studio's MPC-style sampler: pads, voices, chokes, envelopes, kits. No audio device needed (numpy)."""

import os
import sys
import tempfile
import wave
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="fm1-pads-"))
os.environ.update(FM1_LIBRARY=str(TMP / "library"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fm1-bt-bridge"))

import numpy as np  # noqa: E402

import pads  # noqa: E402


def wav(name, frames, rate=48000, value=16000):
    path = TMP / name
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.full(frames, value, dtype="<i2").tobytes())
    return str(path)


def kit(**pads_by_index):
    k = pads.empty_kit()
    for i, settings in pads_by_index.items():
        k["pads"][int(i[1:])].update(settings)
    return k


def test_one_shot_plays_to_its_end():
    e = pads.Engine(48000)
    e.set_kit(kit(p0={"sample": wav("kick.wav", 1000)}))
    e.trigger(0, 127)
    out = e.render(256)
    assert out.shape == (256, 2) and abs(out[100, 0] - 16000 / 32768) < 0.02      # full level, both sides
    assert e.sounding() == [0]
    e.release(0)                                                     # a one-shot ignores the release
    e.render(256)
    assert e.sounding() == [0]
    for _ in range(4):
        e.render(256)
    assert e.sounding() == []                                        # 1000 frames: over


def test_velocity_pan_and_tune():
    e = pads.Engine(48000)
    e.set_kit(kit(p1={"sample": wav("snare.wav", 4800), "pan": 50, "tune": 12}))
    e.trigger(1, 64)
    out = e.render(256)
    assert out[100, 0] == 0 and 0 < out[100, 1] < 16000 / 32768 * 0.5   # panned right, quieter at velocity 64
    frames = 256
    while e.sounding():
        e.render(256)
        frames += 256
    assert frames <= 2600                                            # an octave up: half the length


def test_choke_gate_and_loop():
    e = pads.Engine(48000)
    e.set_kit(kit(p2={"sample": wav("ohat.wav", 48000), "choke": 1},
                  p3={"sample": wav("chat.wav", 2000), "choke": 1},
                  p4={"sample": wav("pad.wav", 48000), "mode": "gate", "release": 10},
                  p5={"sample": wav("loop.wav", 500), "mode": "loop"}))
    e.trigger(2)
    e.render(256)
    e.trigger(3)                                                     # the closed hat cuts the open one
    for _ in range(2):
        e.render(256)
    assert 2 not in e.sounding() and 3 in e.sounding()
    e.trigger(4)
    e.render(256)
    e.release(4)                                                     # a gate pad: stops when let go
    for _ in range(4):
        e.render(256)
    assert 4 not in e.sounding()
    e.trigger(5)
    for _ in range(10):                                              # a 500-frame loop keeps going while held
        e.render(256)
    assert 5 in e.sounding()
    e.midi(bytes([0x80, pads.FIRST_NOTE + 5, 0]))                    # its note let go
    for _ in range(20):
        e.render(256)
    assert 5 not in e.sounding()


def test_midi_notes_and_voices():
    e = pads.Engine(48000)
    e.set_kit(kit(p0={"sample": wav("k.wav", 48000)}))
    e.midi(bytes([0x99, 36, 100]))                                   # C1, any channel: pad 1
    e.midi(bytes([0x90, 35, 100]))                                   # below the pads: nothing
    assert e.sounding() == [0] and len(e.voices) == 1
    for _ in range(pads.VOICES + 5):
        e.trigger(0)
    assert len(e.voices) == pads.VOICES                              # the oldest stopped
    e.set_kit(kit(p1={"sample": wav("cd.wav", 44100, rate=44100)}))  # a 44.1 kHz sample: 48 kHz once, at load
    e.trigger(1)
    assert e.voices[-1]["step"] == 1.0 and abs(len(e.voices[-1]["data"]) - 48000) <= 1
    assert pads.pad_of(51) == 15 and pads.pad_of(52) is None
    pcm = pads.to_pcm(np.array([[2.0, -2.0]], dtype=np.float32))
    assert np.frombuffer(pcm, dtype="<i2").tolist() == [32767, -32767]   # clipped


def test_sloop_layout():
    """the SLOOP layout: SLOOP's drum track's GM notes on channel 10 play the pads in its key order; other
    channels and the MPC notes don't; the PADS tab switches it and turns the FM-1 > SMP route on"""
    assert pads.pad_of(38, 9, "sloop") == 2 and pads.pad_of(70, 9, "sloop") == 13     # SNARE, SHAKER
    assert pads.pad_of(38, 0, "sloop") is None and pads.pad_of(45, 9, "sloop") is None
    assert pads.note_of(15, "sloop") == 56 and pads.note_of(15) == 51
    e = pads.Engine(48000)
    e.set_kit(dict(kit(p2={"sample": wav("sn.wav", 48000)}), map="sloop"))
    e.midi(bytes([0x90, 38, 100]))                                   # a synth track's note: not the pads
    assert e.sounding() == []
    e.midi(bytes([0x99, 38, 100]))                                   # SLOOP's snare: pad 3
    assert e.sounding() == [2]
    assert pads.clean_kit({"map": "weird"})["map"] == "mpc"
    import bridge
    import deskui

    class Display:
        image = theme = None

        def show_rgb(self, img):
            self.image = img
    app = bridge.App(Display(), dict(bridge.DEFAULTS))
    app.save_settings = lambda: None
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.open_tab("PADS")
    ui.render(0.0, force=True)
    assert not app.routes()["FM-1>SMP"]["on"]
    box = next(w[0] for w in ui.widgets if w[3] == "pad-map")
    ui.touch("down", box[0] + 4, box[1] + 4, 0.0)
    ui.touch("up", box[0] + 4, box[1] + 4, 0.0)
    assert app.kit()["map"] == "sloop" and app.routes()["FM-1>SMP"]["on"] and "OUT = SEQ" in app.message


def test_sequencer_timing():
    """SLOOP's drum sequencer: steps on the beat grid, odd steps swung late, ratchets share their step, a nudge
    moves the whole step, the pattern loops at LEN, nothing before PLAY or after STOP"""
    seq = pads.empty_seq()
    seq["len"], seq["swing"] = 4, 50
    seq["steps"][0] = [[0, 0, 1]]
    seq["steps"][1] = [[1, 3, 1]]                                    # HARD
    seq["steps"][2] = [[2, 1, 2]]                                    # GHOST, ratchet x2
    seq["steps"][3] = [[3, 0, 1]]
    seq["micro"][3] = 16                                             # a quarter step late
    sq = pads.Sequencer()
    assert sq.events(seq, 0.0, 1.0) == []                            # no grid: nothing
    per = 60 / (120 * 24)                                            # 120 BPM: a 16th is 0.125 s
    sq.grid = (per, 10.0, True)
    ons = [e for e in sq.events(seq, 10.0, 10.5) if e[1] == "on"]
    sw = 50 * 0.125 / 200                                            # swing 50: odd steps a quarter step late
    want = [(10.0, 0, 100), (10.125 + sw, 1, 127), (10.25, 2, 42), (10.25 + (0.125 + sw) / 2, 2, 42),
            (10.375 + sw + 16 * 0.125 / 64, 3, 100)]
    assert [(round(t, 6), p, v) for t, _, p, v in ons] == [(round(t, 6), p, v) for t, p, v in want]
    assert sq.step == 3
    later = [e for e in sq.events(seq, 10.5, 10.6) if e[1] == "on"]
    assert later and later[0][2] == 0 and abs(later[0][0] - 10.5) < 1e-9   # step 0 again: LEN 4 loops
    sq.grid = (per, None, False)                                     # STOP
    sq.events(seq, 10.6, 10.7)
    assert sq.step is None and sq.pending == []
    seq["div"] = 4                                                   # 8T: three a beat, never swung
    sq.grid = (per, 20.0, True)
    times = [round(t - 20, 6) for t, kind, _, _ in sq.events(seq, 20.0, 20.4) if kind == "on"]
    assert times[:2] == [0.0, round(0.5 / 3, 6)]


def test_sequencer_in_the_engine():
    """a hit starts at its own frame inside a block; live REC puts a played pad on the nearest step (QUANT
    off: with its nudge) and doesn't play it twice"""
    e = pads.Engine(48000)
    k = kit(p0={"sample": wav("seqk.wav", 48000)})
    k["seq"]["steps"][1] = [[0, 0, 1]]
    e.set_kit(k)
    per = 60 / (120 * 24)
    e.seq.grid = (per, 0.0, True)
    out = e.render(512, 0.125 - 100 / 48000)                         # step 1 is due 100 frames into this block
    assert abs(out[99, 0]) < 1e-6 and abs(out[101, 0]) > 0.01
    e.seq.rec = True
    k = e.kit
    assert e.hit(5, 100, now=0.25 + 0.006)                           # just after step 2
    assert [5, 0, 1] in e.kit["seq"]["steps"][2] and e.kit["seq"]["micro"][2] == 0
    assert (2, 5) in e.seq.skip                                      # heard as played: not again on step 2
    e.kit["seq"]["quant"] = False
    e.hit(6, 50, now=0.375 - 0.01)                                   # a little early for step 3, softly: GHOST
    assert [6, 1, 1] in e.kit["seq"]["steps"][3] and e.kit["seq"]["micro"][3] == -5
    e.seq.rec = False
    assert not e.hit(7, 100, now=0.5)


def test_note_repeat():
    """note repeat: a held pad plays again at the repeat's division, on the grid while the sequencer plays,
    from the press while stopped; let go, it stops; recorded with live REC"""
    e = pads.Engine(48000)
    e.set_kit(kit(p0={"sample": wav("rep.wav", 4800)}))
    per = 60 / (120 * 24)
    e.seq.grid = (per, None, False)                                  # stopped, 120 BPM
    e.seq.repeat = 2                                                 # 1/16: every 0.125 s
    e.hit(0, 90, now=5.0)
    ons = [ev for ev in e.seq.events(e.kit["seq"], 5.0, 5.4) if ev[1] == "on"]
    assert [round(t, 6) for t, _, _, _ in ons] == [5.125, 5.25, 5.375] and ons[0][3] == 90
    e.seq.let_go(0)
    assert e.seq.events(e.kit["seq"], 5.4, 6.0) == []
    e.seq.grid, e.seq.rec, e.seq.repeat = (per, 10.0, True), True, 1    # playing from 10.0, 1/8
    e.hit(1, 100, now=10.26)                                          # held from just after beat 1's 8th
    ons = [ev for ev in e.seq.events(e.kit["seq"], 10.26, 10.8) if ev[1] == "on" and ev[2] == 1]
    assert [round(t, 6) for t, _, _, _ in ons] == [10.5, 10.75]       # on the 8ths of the grid
    steps = e.kit["seq"]["steps"]
    assert [1, 0, 1] in steps[2] and [1, 0, 1] in steps[4] and [1, 0, 1] in steps[6] and e.seq.dirty
    e.seq.repeat = None
    assert not [ev for ev in e.seq.events(e.kit["seq"], 10.8, 11.0) if ev[2] == 1]


def test_seq_page():
    """the SEQ page: pick a pad, tap steps, hold one for its level and ratchet, pages, LEN, DIV, QUANT, PLAY
    makes the box the clock when there's none to follow, REC goes to the engine"""
    import bridge
    import deskui

    class Display:
        image = theme = None

        def show_rgb(self, img):
            self.image = img

    class Pads:
        sounding, step = [], None

        def __init__(self):
            self.sent = []

        def send(self, *msg):
            self.sent.append(msg)

    class Midi:
        clock = None

        def __init__(self):
            self.sent = []

        def set_clock(self, mode, bpm):
            self.sent.append(("clock", mode, bpm))

        def transport(self, what):
            self.sent.append(("transport", what))
    app = bridge.App(Display(), dict(bridge.DEFAULTS))
    app.save_settings, app.pads, app.midi = (lambda: None), Pads(), Midi()
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.open_tab("PADS")
    ui.render(0.0, force=True)

    def touch(key, hold=0.0):
        box = next(w[0] for w in ui.widgets if w[3] == key)
        ui.touch("down", box[0] + 4, box[1] + 4, 0.0)
        if hold:
            ui.touch("move", box[0] + 5, box[1] + 4, hold)
        ui.touch("up", box[0] + 4, box[1] + 4, hold)
        ui.render(0.0, force=True)
    touch("pad-repeat")
    touch("pad-repeat")
    assert app.state["repeat"] == 2 and app.pads.sent[-1] == ("repeat", 2)      # RPT 1/16
    touch("seq")
    assert ui.seq_open and ui.pads_screen()
    touch(("seq-pad", 2))
    assert ui.seq_pad == 2 and app.pads.sent[-1] == ("tap", 2, 100)
    touch(("seq-step", 4))
    assert app.kit()["seq"]["steps"][4] == [[2, 0, 1]]
    touch(("seq-step", 4), hold=0.7)                                 # held: its sheet
    assert ui.seq_edit == 4
    touch(("seq-row", "LEVEL"))
    touch(("seq-row", "RATCHET"))
    touch("seq-nudge+")
    assert app.kit()["seq"]["steps"][4] == [[2, 3, 2]] and app.kit()["seq"]["micro"][4] == 4
    touch("seq-done")
    for _ in range(16):
        touch("seq-len+")
    assert app.kit()["seq"]["len"] == 32 and ("seq-page", 1) in [w[3] for w in ui.widgets]
    touch(("seq-page", 1))
    touch(("seq-step", 20))
    assert app.kit()["seq"]["steps"][20] == [[2, 0, 1]]
    touch("seq-div")
    touch("seq-quant")
    assert app.kit()["seq"]["div"] == 3 and app.kit()["seq"]["quant"] is False
    touch("seq-rec")
    assert app.pads.sent[-1] == ("rec", True)
    touch("seq-play")
    assert app.state["clock_mode"] == "box" and ("transport", "start") in app.midi.sent
    assert pads.clean_kit(app.state["kit"]) == app.kit()             # it all saves with the kit


def test_browse_and_the_editor():
    """the sample editor lives with the sampler: EDITOR opens it (its OPEN list the first time, then the file
    last edited), OPEN lists the samples and takes, BACK goes list > take > sampler, TO PAD saves the edit onto a
    pad and opens its page; MORE has no editor of its own"""
    import time as clock
    import bridge
    import deskui
    import library

    class Display:
        image = theme = None

        def show_rgb(self, img):
            self.image = img

    class Pads:
        sounding, step = [], None

        def send(self, *msg):
            pass
    t = np.arange(24000) / 48000
    with wave.open(str(library.folder("samples") / "tone.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((np.sin(2 * np.pi * 220 * t) * 12000).astype("<i2").tobytes())
    app = bridge.App(Display(), dict(bridge.DEFAULTS))
    app.save_settings, app.pads = (lambda: None), Pads()
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    assert "SAMPLE EDITOR" not in [m[0] for m in deskui.MORE]
    ui.open_tab("PADS")
    ui.render(0.0, force=True)

    def touch(key):
        ui.render(0.0, force=True)
        box = next(w[0] for w in ui.widgets if w[3] == key)
        ui.touch("down", box[0] + 4, box[1] + 4, 0.0)
        ui.touch("up", box[0] + 4, box[1] + 4, 0.0)
        ui.render(0.0, force=True)

    def opened():
        for _ in range(200):
            if not app.ed["loading"]:
                return
            clock.sleep(0.02)
    touch("editor")                                                  # EDITOR, the first time: its OPEN list
    assert app.screen == "editor" and app.ed is None and ui.ed_files
    touch(("browse", "samples"))
    touch(("file", "tone.wav"))
    opened()
    assert app.screen == "editor" and app.ed["name"] == "tone.wav" and not ui.ed_files
    touch("ed-open")                                                 # OPEN again: the list over the take
    assert ui.ed_files
    touch("ed-back")                                                 # BACK: to the take
    assert not ui.ed_files and app.ed["name"] == "tone.wav"
    touch("ed-back")                                                 # BACK again: the sampler
    assert app.screen == "status" and ui.tab == "PADS"
    touch("editor")                                                  # EDITOR again: the file last edited
    opened()
    assert app.ed["name"] == "tone.wav" and not ui.ed_files
    touch("ed-to-pad")
    touch(("ed-pad", 7))
    pad = app.kit()["pads"][7]
    assert pad["sample"] and Path(pad["sample"]).exists() and Path(pad["sample"]).name != "tone.wav"
    assert app.screen == "status" and ui.tab == "PADS" and ui.pad_edit == 7


def test_kits():
    k = pads.clean_kit({"name": "BOOM BAP", "pads": [{"sample": "x.wav", "level": 999, "mode": "weird"}]})
    assert len(k["pads"]) == 16 and k["pads"][0]["level"] == 200 and k["pads"][0]["mode"] == "one-shot"
    path = pads.save_kit(k)
    assert path.name == "BOOM-BAP.json" and pads.list_kits() == ["BOOM-BAP"]
    assert pads.load_kit(path)["pads"][0]["sample"] == "x.wav"


def test_pads_tab():
    """the PADS tab: a tap plays a pad (held: until let go), EDIT opens its page, - / + and choices change it,
    a sample picked goes on it, kits save, load and start afresh"""
    import bridge
    import deskui
    import library

    class Display:
        image = theme = None

        def show_rgb(self, img):
            self.image = img

    class Pads:
        sounding = []

        def __init__(self):
            self.sent = []

        def send(self, *msg):
            self.sent.append(msg)
    app = bridge.App(Display(), dict(bridge.DEFAULTS))
    app.save_settings, app.pads = (lambda: None), Pads()
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.open_tab("PADS")
    ui.render(0.0, force=True)

    def touch(key, hold=False):
        box = next(w[0] for w in ui.widgets if w[3] == key)
        ui.touch("down", box[0] + 4, box[1] + 4, 0.0)
        if not hold:
            ui.touch("up", box[0] + 4, box[1] + 4, 0.0)
        ui.render(0.0, force=True)
    touch(("pad", 1))                                                # an empty pad: its page, to set it up
    assert ui.pad_edit == 1 and not app.pads.sent
    touch("pad-back")
    k = app.kit()
    k["pads"][0]["sample"] = wav("hit.wav", 1000)
    app.set_kit(k)
    ui.render(0.0, force=True)
    touch(("pad", 0), hold=True)                                     # held: sounding, not yet let go
    assert app.pads.sent[-1] == ("tap", 0, 110)
    ui.touch("up", 0, 0, 0.0)
    assert app.pads.sent[-1] == ("untap", 0)
    touch("pad-mode")                                                # EDIT, then pad 5
    touch(("pad", 4))
    assert ui.pad_edit == 4 and ui.pads_screen()
    touch(("pad+", "level"))
    touch(("pad-", "tune"))
    touch("pad-down")                                                # MODE is further down the page
    touch(("pad-row", "mode"))
    touch("pad-up")
    pad = app.kit()["pads"][4]
    assert (pad["level"], pad["tune"], pad["mode"]) == (105, -1, "gate")
    wav("snare.wav", 1000)
    sample = library.folder("samples") / "snare.wav"
    sample.write_bytes((TMP / "snare.wav").read_bytes())
    touch(("pad-row", "sample"))                                     # the samples folder, as a Studio page
    assert app.screen == "pick"
    app.pick["on"]("snare.wav")
    ui.render(0.0, force=True)
    assert app.kit()["pads"][4]["sample"] == str(sample) and app.screen == "status"
    assert app.pads.sent[-1][0] == "kit"                             # the engine has the kit
    touch("pad-back")
    touch("kits")
    touch("kit-save")
    assert "KIT-1" in pads.list_kits()
    touch("kit-new")
    assert app.kit()["name"] == "KIT 2" and not app.kit()["pads"][4]["sample"]
    touch("kits")
    touch(("kit", "KIT-1"))
    assert app.kit()["pads"][4]["sample"] == str(sample)             # loaded back


def test_sampler_in_the_box():
    """the sampler's notes from the MIDI routes (SMP), and its sound a source in the mixer"""
    import blemidi
    import bridge

    class Conn:
        def __init__(self):
            self.sent = []

        def send(self, msg):
            self.sent.append(msg)
    b = blemidi.MidiBridge(["felucca"], pads=Conn())
    b.to_ble = lambda m: None
    b.controllers.send_messages = lambda messages: None
    b.from_controllers(bytes([0x99, 36, 100]))                       # USB keys > SMP: on at first
    assert b.pads.sent == [("midi", [[0x99, 36, 100]])]
    assert blemidi.default_routes()["FM-1>SMP"]["on"] is False
    node = {"type": "PipeWire:Interface:Node", "id": 70,
            "info": {"props": {"media.class": "Audio/Source", "node.name": "fm1-sampler"}}}
    port = {"type": "PipeWire:Interface:Port", "id": 71,
            "info": {"props": {"node.id": 70, "port.name": "capture_FL", "port.direction": "out", "port.id": 0}}}
    _, _, _, others = bridge.pick_nodes([node, port], ["felucca"])
    assert others[0]["label"] == "SAMPLER" and bridge.device_keys(others) == ["u-sampler"]


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")
