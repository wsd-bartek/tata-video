"""Single source of truth for the film's timing.

The Polish voice-over is the master clock. All phrase times below were measured
from assets/voiceover/voiceover.mp3 (pause detection + Whisper transcription of
each phrase). Shots are cut inside the natural pauses of the narration.

VO_OFFSET shifts the voice-over into the film so the opening image can breathe
for a moment before the first word.
"""

FPS = 24
WIDTH, HEIGHT = 1920, 1080
VO_OFFSET = 1.5  # seconds of image + music before the first spoken word

# Narration, in voice-over time (seconds).
PHRASES = [
    (0.00, 4.28, "Piątego października tysiąc dziewięćset osiemdziesiątego pierwszego roku"),
    (4.57, 6.20, "rozpoczęła się jego historia."),
    (6.95, 9.07, "Czterdzieści pięć lat pełnych radości,"),
    (9.33, 10.63, "wyzwań i wspomnień."),
    (11.47, 13.04, "W 2008 roku"),
    (13.33, 15.01, "pojawił się ktoś wyjątkowy –"),
    (15.29, 16.74, "jego syn, Bartek."),
    (17.21, 19.76, "Od tego dnia ich historia stała się wspólna."),
    (20.25, 20.74, "Dziś,"),
    (21.00, 24.08, "5 października 2026 roku."),
    (24.50, 26.76, "Świętujemy czterdzieści pięć lat życia."),
    (27.20, 27.76, "Tato,"),
    (28.02, 29.16, "wszystkiego najlepszego."),
    (29.50, 31.66, "Najlepsze rozdziały dopiero się rozpoczną."),
]
VO_DURATION = 31.84


def film(t_vo):
    """Voice-over time -> film time."""
    return round(t_vo + VO_OFFSET, 3)


# Shot list. Times are in voice-over time; every cut sits in a narration pause.
# kind:  "still" = picture with a slow camera move,
#        "video" = generated moving plate (falls back to the still if missing).
# move:  (zoom_start, zoom_end, pan_x_start, pan_x_end, pan_y_start, pan_y_end);
#        pan values are fractions of the free margin (-1 .. 1, 0 = centred).
# grade: name of a look in render.py GRADES.
# light: optional low sun / window light added after the grade:
#        (x, y, radius, (r, g, b), strength) - position and radius in frame widths.
# photo: optional basename in assets/photos/ - a real family photo there
#        replaces the generated picture for this shot.
SHOTS = [
    dict(id="s01_fog1981", start=-VO_OFFSET, end=4.42, kind="video", grade="dawn",
         move=(1.02, 1.08, 0.00, 0.00, 0.10, 0.00),
         note="1981 - darkness, fog over the fields, lone farmhouse, first light"),
    dict(id="s02_homestead", start=4.42, end=6.80, kind="still", grade="dawn_warm",
         move=(1.04, 1.12, -0.30, 0.15, 0.10, -0.05),
         light=(0.80, 0.34, 0.95, (255, 160, 84), 0.62),
         note="rozpoczęła się jego historia - old homestead, first sun"),
    dict(id="s03_road", start=6.80, end=9.20, kind="still", grade="memory",
         move=(1.03, 1.10, 0.10, -0.10, 0.10, 0.00),
         note="pełnych radości - Fiat 126p on a village road, late 80s"),
    dict(id="s04_memories", start=9.20, end=11.30, kind="still", grade="memory",
         move=(1.42, 1.52, -0.35, -0.55, -0.80, -0.90),
         note="wyzwań i wspomnień - watch, glasses, old family photographs"),
    dict(id="s05_nursery2008", start=11.30, end=13.20, kind="still", grade="warm",
         move=(1.03, 1.10, -0.50, -0.70, 0.00, 0.00),
         note="W 2008 roku - the light turns warm"),
    dict(id="s06_hands", start=13.20, end=17.05, kind="still", grade="warm",
         move=(1.02, 1.17, -0.10, -0.30, 0.00, 0.10), photo="bartek_baby",
         note="ktoś wyjątkowy - jego syn, Bartek (slow push-in)"),
    dict(id="s07_together", start=17.05, end=20.05, kind="still", grade="warm_soft",
         move=(1.05, 1.12, 0.00, 0.00, 0.15, -0.05), photo="together",
         light=(0.66, 0.22, 0.95, (255, 172, 96), 0.52),
         note="Od tego dnia ich historia stała się wspólna"),
    dict(id="s08_golden2026", start=20.05, end=27.00, kind="video", grade="golden",
         move=(1.02, 1.08, 0.00, 0.00, 0.00, -0.10),
         note="Dziś, 5 października 2026 - bright and golden, titles"),
    dict(id="s09_tato", start=27.00, end=29.35, kind="still", grade="golden_close",
         move=(1.03, 1.11, 0.00, 0.05, -0.10, -0.20), photo="best",
         note="Tato, wszystkiego najlepszego - father and son"),
    dict(id="s10_road_ahead", start=29.35, end=34.40, kind="video", grade="hope",
         move=(1.02, 1.08, 0.00, 0.00, 0.00, -0.05),
         note="Najlepsze rozdziały dopiero się rozpoczną - road into the light"),
]

# Cross-dissolve length (seconds) into each shot.
DISSOLVE = {
    "s02_homestead": 0.7,
    "s03_road": 0.6,
    "s04_memories": 0.6,
    "s05_nursery2008": 0.8,
    "s06_hands": 0.9,
    "s07_together": 0.8,
    "s08_golden2026": 0.9,
    "s09_tato": 0.8,
    "s10_road_ahead": 0.9,
}

FADE_IN = (0.0, 2.2)          # film time: black -> first picture
FADE_TO_BLACK = (33.0, 34.4)  # voice-over time
END_CARD = dict(start=35.1, end=41.6, fade=1.3)  # voice-over time, on black
TAIL = 1.4                    # black after the end card has faded out

# Titles over the picture, voice-over time.
TITLES = [
    dict(text="05.10.1981  —  05.10.2026", start=23.00, end=25.25, fade=0.7,
         font="CormorantGaramond-Medium-Lining.ttf", size=66, tracking=0.16, y=0.50),
    dict(text="45 LAT", start=25.45, end=27.15, fade=0.6,
         font="CormorantGaramond-Regular-Lining.ttf", size=140, tracking=0.34, y=0.50),
]

# End card on black. offset = delay after END_CARD start.
END_TITLES = [
    dict(text="45 LAT", offset=0.0, font="CormorantGaramond-Light-Lining.ttf", size=150,
         tracking=0.34, y=0.455),
    dict(text="Najlepsze rozdziały dopiero się rozpoczną.", offset=1.1,
         font="CormorantGaramond-LightItalic.ttf", size=44, tracking=0.03, y=0.585),
]


def total_duration():
    return film(END_CARD["end"]) + TAIL
