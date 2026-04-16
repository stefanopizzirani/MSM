# Generates the GUITAR_CHORDS library dynamically.
# 3 variations per chord

E_STR = {"E":0, "F":1, "F#":2, "Gb":2, "G":3, "G#":4, "Ab":4, "A":5, "A#":6, "Bb":6, "B":7, "C":8, "C#":9, "Db":9, "D":10, "D#":11, "Eb":11}
A_STR = {"A":0, "A#":1, "Bb":1, "B":2, "C":3, "C#":4, "Db":4, "D":5, "D#":6, "Eb":6, "E":7, "F":8, "F#":9, "Gb":9, "G":10, "G#":11, "Ab":11}

shapes_E = {
    "": lambda f: [f, f+2, f+2, f+1, f, f],
    "m": lambda f: [f, f+2, f+2, f, f, f],
    "7": lambda f: [f, f+2, f, f+1, f, f],
    "m7": lambda f: [f, f+2, f, f, f, f],
    "maj7": lambda f: [f, 'X', f+1, f+1, f, 'X'],
    "sus2": lambda f: [f, f+2, f+4, f+1, f, f],
    "sus4": lambda f: [f, f+2, f+2, f+2, f, f],
    "add9": lambda f: [f, f+2, f+4, f+1, f, f],
    "6": lambda f: [f, f+2, f+2, f+1, f+2, f],
    "m6": lambda f: [f, f+2, f+2, f, f+2, f],
    "9": lambda f: [f, f+2, f, f+1, f+2, f+2],
    "m9": lambda f: [f, f+2, f, f, f, f+2],
    "11": lambda f: [f, 'X', f, f+1, f, f],
    "13": lambda f: [f, 'X', f, f+1, f+2, f],
    "+": lambda f: [f, 'X', f+2, f+1, f+1, f],
    "aug": lambda f: [f, 'X', f+2, f+1, f+1, f],
    "dim": lambda f: [f, 'X', f+2, f, f+1, f],
    "dim7": lambda f: [f, 'X', f+2, f, f+1, f-1],
    "5": lambda f: [f, f+2, f+2, 'X', 'X', 'X'],
    "7sus4": lambda f: [f, f+2, f, f+2, f, f],
    "m7b5": lambda f: ['X', f, f, f-1, f, 'X'],
    "2": lambda f: [f, f+2, f+4, f+1, f, f], # Alias for sus2
    "4": lambda f: [f, f+2, f+2, f+2, f, f]  # Alias for sus4
}

shapes_A = {
    "": lambda f: ['X', f, f+2, f+2, f+2, f],
    "m": lambda f: ['X', f, f+2, f+2, f+1, f],
    "7": lambda f: ['X', f, f+2, f, f+2, f],
    "m7": lambda f: ['X', f, f+2, f, f+1, f],
    "maj7": lambda f: ['X', f, f+2, f+1, f+2, f],
    "sus2": lambda f: ['X', f, f+2, f+2, f, f],
    "sus4": lambda f: ['X', f, f+2, f+2, f+3, f],
    "add9": lambda f: ['X', f, f+2, f+4, f+2, f],
    "6": lambda f: ['X', f, f+2, f+2, f+2, f+2],
    "m6": lambda f: ['X', f, f+2, f, f+1, f+2],
    "9": lambda f: ['X', f, f+2, f, f+3, f+3],
    "m9": lambda f: ['X', f, f+2, f, f+1, f+3],
    "11": lambda f: ['X', f, f+2, f, f+3, f],
    "13": lambda f: ['X', f, f+2, f, f+2, f+2],
    "+": lambda f: ['X', f, f+3, f+2, f+2, f],
    "aug": lambda f: ['X', f, f+3, f+2, f+2, f],
    "dim": lambda f: ['X', f, f+1, f+2, f+1, 'X'],
    "dim7": lambda f: ['X', f, f+1, f+2, f+1, f+2],
    "5": lambda f: ['X', f, f+2, f+2, 'X', 'X'],
    "7sus4": lambda f: ['X', f, f+2, f, f+3, f],
    "m7b5": lambda f: ['X', f, f+1, f, f+1, 'X'],
    "2": lambda f: ['X', f, f+2, f+2, f, f], # Alias for sus2
    "4": lambda f: ['X', f, f+2, f+2, f+3, f] # Alias for sus4
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
types = ["", "m", "7", "m7", "maj7", "sus2", "sus4", "add9", "6", "m6", "9", "m9", "11", "13", "+", "aug", "dim", "dim7", "5", "7sus4", "m7b5", "2", "4"]

SPECIAL_CHORDS = {
    "D/F#": [[2, 0, 0, 2, 3, 2]],
    "G/B": [['X', 2, 0, 0, 0, 3]],
    "C/G": [[3, 3, 2, 0, 1, 0]],
    "E/G#": [[4, 2, 2, 1, 0, 0]],
    "E/C#": [['X', 4, 2, 1, 0, 0]],
    "A/C#": [['X', 4, 2, 2, 2, 0]],
    "Am/G": [[3, 0, 2, 2, 1, 0]],
    "F/G": [[3, 'X', 3, 2, 1, 1]],
    "Asus2/E": [[0, 0, 2, 2, 0, 0]],
    "Bb/F": [[1, 1, 3, 3, 3, 1]],
    "C/B": [['X', 2, 2, 0, 1, 0]],
    "Dsus2/A": [['X', 0, 0, 2, 3, 0]],
    "Em/F#": [[2, 2, 2, 0, 0, 0]],
    "Fmaj7/A": [['X', 0, 3, 2, 1, 0]],
    "F#m/C#": [['X', 4, 4, 2, 2, 2]],
    "G/F#": [[2, 0, 0, 0, 0, 3]],
    "Gadd11/B": [['X', 2, 0, 0, 1, 3]]
}

ITALIAN_MAP = {
    "DO": "C", "RE": "D", "MI": "E", "FA": "F", "SOL": "G", "LA": "A", "SI": "B"
}

GUITAR_CHORDS = {}

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
        
        # Sort variations by lowest fret ascending before capping to 3
        def min_fret(v):
            frets = [x for x in v if isinstance(x, int) and x > 0]
            # Open chords might report a fret of 1 or 2, but we prioritize them first.
            if 0 in v or (len(frets) > 0 and min(frets) <= 2 and 'X' in v[:2]):
                return 0 
            return min(frets) if frets else 0
            
        voicings.sort(key=min_fret)
        
        GUITAR_CHORDS[name] = voicings[:3]

# Inject Special Slash Chords
GUITAR_CHORDS.update(SPECIAL_CHORDS)

# Generate Italian Aliases
italian_chords = {}
for name, variations in GUITAR_CHORDS.items():
    for ital, eng in ITALIAN_MAP.items():
        if name.startswith(eng):
            ital_name = name.replace(eng, ital, 1)
            italian_chords[ital_name] = variations
GUITAR_CHORDS.update(italian_chords)

# Specific Aliases and cleanup (*)
for alias, target in [("DM", "D"), ("Bb4", "Bbsus4"), ("Asus2", "A2"), ("Asus4", "A4"), 
                      ("sus", "sus4"), ("sus7", "7sus4"), ("7+", "7aug")]:
    if target in GUITAR_CHORDS:
        GUITAR_CHORDS[alias] = GUITAR_CHORDS[target]
    # Also handle aliases with roots
    for r in roots:
        r_alias = r + alias
        r_target = r + target
        if r_target in GUITAR_CHORDS:
            GUITAR_CHORDS[r_alias] = GUITAR_CHORDS[r_target]

# Handle '*' suffix from ChordPro
star_chords = {}
for name, variations in GUITAR_CHORDS.items():
    star_chords[name + "*"] = variations
GUITAR_CHORDS.update(star_chords)