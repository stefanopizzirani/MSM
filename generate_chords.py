import json

E_STR = {"E":0, "F":1, "F#":2, "Gb":2, "G":3, "G#":4, "Ab":4, "A":5, "A#":6, "Bb":6, "B":7, "C":8, "C#":9, "Db":9, "D":10, "D#":11, "Eb":11}
A_STR = {"A":0, "A#":1, "Bb":1, "B":2, "C":3, "C#":4, "Db":4, "D":5, "D#":6, "Eb":6, "E":7, "F":8, "F#":9, "Gb":9, "G":10, "G#":11, "Ab":11}

def apply_shape(base, fret):
    res = []
    for x in base:
        if x == 'X': res.append('X')
        elif x == 0 and fret > 0: res.append(fret)
        else: res.append(x + fret)
    return res

shapes_E = {
    "": lambda f: [f, f+2, f+2, f+1, f, f],
    "m": lambda f: [f, f+2, f+2, f, f, f],
    "7": lambda f: [f, f+2, f, f+1, f, f],
    "m7": lambda f: [f, f+2, f, f, f, f],
    "maj7": lambda f: [f, 'X', f+1, f+1, f, 'X']
}

shapes_A = {
    "": lambda f: ['X', f, f+2, f+2, f+2, f],
    "m": lambda f: ['X', f, f+2, f+2, f+1, f],
    "7": lambda f: ['X', f, f+2, f, f+2, f],
    "m7": lambda f: ['X', f, f+2, f, f+1, f],
    "maj7": lambda f: ['X', f, f+2, f+1, f+2, f]
}

open_dict = {
    "C": ['X',3,2,0,1,0], "Cm": ['X',3,5,5,4,3], "C7": ['X',3,2,3,1,0], "Cm7": ['X',3,5,3,4,3], "Cmaj7": ['X',3,2,0,0,0],
    "D": ['X','X',0,2,3,2], "Dm": ['X','X',0,2,3,1], "D7": ['X','X',0,2,1,2], "Dm7": ['X','X',0,2,1,1], "Dmaj7": ['X','X',0,2,2,2],
    "E": [0,2,2,1,0,0], "Em": [0,2,2,0,0,0], "E7": [0,2,0,1,0,0], "Em7": [0,2,2,0,3,0], "Emaj7": [0,2,1,1,0,0],
    "G": [3,2,0,0,0,3], "Gm": [3,5,5,3,3,3], "G7": [3,2,0,0,0,1], "Gm7": [3,5,3,3,3,3], "Gmaj7": [3,2,0,0,0,2],
    "A": ['X',0,2,2,2,0], "Am": ['X',0,2,2,1,0], "A7": ['X',0,2,0,2,0], "Am7": ['X',0,2,0,1,0], "Amaj7": ['X',0,2,1,2,0],
    "F": [1,3,3,2,1,1], "Fm": [1,3,3,1,1,1], "F7": [1,3,1,2,1,1], "Fm7": [1,3,1,1,1,1], "Fmaj7": ['X','X',3,2,1,0],
    "B": ['X',2,4,4,4,2], "Bm": ['X',2,4,4,3,2], "B7": ['X',2,1,2,0,2], "Bm7": ['X',2,4,2,3,2], "Bmaj7": ['X',2,4,3,4,2]
}

roots = ["C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B"]
types = ["", "m", "7", "m7", "maj7"]

lib = {}

for r in roots:
    for t in types:
        name = r + t
        voicings = []
        if name in open_dict and open_dict[name][0] != 1 and open_dict[name][1] != 1: 
            voicings.append(open_dict[name])
        
        e_fret = E_STR[r]
        e_v = shapes_E[t](e_fret)
        if e_fret == 0 and e_v not in voicings:
            voicings.append(e_v)
        elif e_fret > 0:
            if e_v not in voicings: voicings.append(e_v)
            
        a_fret = A_STR[r]
        a_v = shapes_A[t](a_fret)
        if a_fret == 0 and a_v not in voicings:
            voicings.append(a_v)
        elif a_fret > 0:
            if a_v not in voicings: voicings.append(a_v)
            
        if len(voicings) < 3:
            if e_fret <= 4:
                e_v_hi = shapes_E[t](e_fret + 12)
                if e_v_hi not in voicings: voicings.append(e_v_hi)
            elif a_fret <= 4:
                a_v_hi = shapes_A[t](a_fret + 12)
                if a_v_hi not in voicings: voicings.append(a_v_hi)
        
        lib[name] = voicings[:3]

with open("/home/spizzirani/Documents/MSM2/ui/chord_lib.py", "w") as f:
    f.write("# Generated chord library with 3 variations per chord\\n")
    s = json.dumps(lib, indent=4)
    s = s.replace('"X"', "'X'")
    f.write(f"GUITAR_CHORDS = {s}")
