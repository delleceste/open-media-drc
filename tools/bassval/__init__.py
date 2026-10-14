"""bassval — validation of the bass depth detector against known geometry.

The detector (tools/bass_depth.py) reads cues from a stereo recording:
L/R coherence of the bass against the orchestra, the strength of the bass
fundamental, left/right balance, and a double arrival from an unaligned
spot microphone.  Whether those cues measure how far back the basses are
cannot be settled by listening alone.  This package builds recordings where
the answer is known and checks the cues against it:

  stems.py     anechoic orchestra parts (Aalto, Pätynen et al. 2008), one
               player per part, grouped into sections of several players
  geometry.py  the hall, the orchestra layout and the microphone setups
  room.py      room impulse responses: image sources for the early sound,
               a statistical diffuse tail with the coherence a microphone
               pair would see in a diffuse field
  render.py    the stereo recording a main pair (and optionally a bass spot
               microphone, delay-aligned or not) would make of a scene
  scenes.py    a grid of scenes and the ground truth of each
  evaluate.py  the detector on every scene, and every cue against the truth
  __main__.py  command line: python3 -m bassval --help (from tools/)

Every algorithm is described in doc/BASS-DEPTH-ALGORITHMS.md.  The audio
is not part of the repository: the Aalto recordings are free for academic
research and are read from wherever they were unpacked.
"""
