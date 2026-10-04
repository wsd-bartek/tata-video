#!/usr/bin/env bash
# Encode delivery versions from the rendered master.
#   HD:      1080p, ~9 Mbit/s (TV, laptop)
#   kompakt: 1080p, ~4 Mbit/s (phone, messenger)
# Usage: scripts/deliver.sh build/long/film_long.mp4 build/long/Tata_45_LAT
set -euo pipefail
src="$1"
base="$2"
ffmpeg -v error -y -i "$src" -c:v libx264 -preset slow -crf 20 -maxrate 10M -bufsize 20M -tune grain \
  -pix_fmt yuv420p -c:a aac -b:a 256k -movflags +faststart "${base}.mp4"
ffmpeg -v error -y -i "$src" -c:v libx264 -preset slow -crf 23 -maxrate 4M -bufsize 8M -tune film \
  -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart "${base}_kompakt.mp4"
ls -la "${base}.mp4" "${base}_kompakt.mp4"
