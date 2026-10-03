# 45 LAT — Tata

Kurzer cinematic Geburtstagsfilm für Tatas 45. Geburtstag (05.10.1981 — 05.10.2026),
erzählt wie eine alte polnische Familienchronik. Die polnische Voice-over-Datei
(`assets/voiceover/voiceover.mp3`) ist der zeitliche Master: jeder Schnitt liegt in
einer Sprechpause.

**Format:** 1920×1080, 16:9, 24 fps, 35mm-Look (Grading pro Kapitel, Halation,
Vignette, Film Grain, leichter Bildstand-Wackler), 44,5 s.

**Ergebnis:** liegt in der Higgsfield-Medienbibliothek —
`tata_45_lat.mp4` (Master, H.264 CRF 17, ~240 MB) und
`tata_45_lat_kompakt.mp4` (~24 MB, zum Verschicken). Die Videodateien selbst sind
nicht im Repo (Größe, und das Repo ist öffentlich).

## Dramaturgie

| Film-Zeit | Voice-over | Bild | Look |
|---|---|---|---|
| 0:00 | *(Musik, Aufblende)* „Piątego października 1981 roku …“ | Nebel über Feldern, einsames Haus, erstes Licht (Bewegtbild) | kalt, dunkel |
| 0:06 | „… rozpoczęła się jego historia.“ | altes Holzhaus mit Zaun und Obstgarten | Morgen |
| 0:08 | „Czterdzieści pięć lat pełnych radości,“ | Fiat 126p auf einer Dorfstraße, späte 80er | erdig, verblichen |
| 0:11 | „wyzwań i wspomnień.“ | alte Familienfotos, Uhr, Brille | erdig, verblichen |
| 0:13 | „W 2008 roku“ | warmes Morgenlicht, Kinderbett | **deutlich wärmer** |
| 0:15 | „pojawił się ktoś wyjątkowy – jego syn, Bartek.“ | Babyhand hält Vaters Finger, langsamer Push-in | warm |
| 0:19 | „Od tego dnia ich historia stała się wspólna.“ | Vater und kleiner Sohn Hand in Hand auf dem Feldweg | warm |
| 0:22 | „Dziś, 5 października 2026 roku. Świętujemy czterdzieści pięć lat życia.“ | goldene Herbstallee (Bewegtbild) · Titel **05.10.1981 — 05.10.2026**, dann **45 LAT** | hell, golden |
| 0:29 | „Tato, wszystkiego najlepszego.“ | Vater und Sohn, Arm um die Schulter, Gegenlicht | golden |
| 0:31 | „Najlepsze rozdziały dopiero się rozpoczną.“ | Straße zum Sonnenaufgang (Bewegtbild) → Schwarzblende | hoffnungsvoll |
| 0:37 | — | **45 LAT** · *Najlepsze rozdziały dopiero się rozpoczną.* | Schwarz |

Gesichter bleiben bewusst unsichtbar (Hände, Rückenansicht, Gegenlicht), damit keine
fremden KI-Gesichter die Familie „spielen“. Echte Fotos ersetzen die Szenen automatisch,
siehe [`assets/photos/README.md`](assets/photos/README.md).

## Musik

Eigene Komposition (`scripts/compose_music.py`): tiefe Streicher (Celli, Kontrabässe,
langsame Streicher) und leichtes Klavier, gerendert mit FluidSynth. D-Moll im dunklen
Beginn, warmer Wechsel nach F-Dur bei „W 2008 roku“, hohe Streicher bei „Dziś … 2026“,
langer F-Dur-Schlussakkord unter der Schlusstafel. In der Mischung liegt die Musik im
Schnitt ~14 dB unter der Stimme und duckt bei jedem Wort zusätzlich (Sidechain).

## Bauen

```bash
python3 scripts/compose_music.py   # Partitur -> assets/audio/score.{mid,flac}   (braucht fluidsynth + FluidR3_GM)
python3 scripts/mix_audio.py       # Stimme + Musik -> assets/audio/mix.flac
python3 scripts/fetch_assets.py    # generierte Bilder/Clips laut assets/plates/manifest.json laden
python3 scripts/render.py          # -> build/film.mp4
```

Nützlich beim Feintuning: `render.py --stills 3 25 38` (einzelne Frames als JPEG),
`render.py --range 20 28` (nur ein Abschnitt). Alle Zeiten, Schnitte, Titel und
Kamerafahrten stehen zentral in `scripts/timeline.py`.

## Quellen

- Bilder: Higgsfield Cinema Studio Image 2.5 (Standbilder), Minimax Hailuo 2.3 Fast
  (Bewegtbild aus Standbild) — Prompts in der Higgsfield-Historie.
- Schrift: Cormorant Garamond (SIL Open Font License, `assets/fonts/OFL.txt`), Ziffern
  auf Versalziffern umgestellt (`scripts/make_lining_fonts.py`).
- Klang: FluidR3 GM SoundFont (MIT).
