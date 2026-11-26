#!/bin/sh
set -eu
# Encode once so publishing a loop costs only a paced packet copy on the VM.
source=/inputs/${CAMERA}.mp4
digest=$(sha256sum "$source" | cut -d' ' -f1)
file=/cache/${CAMERA}-$digest.mp4
if [ ! -s "$file" ]; then
    ffmpeg -nostdin -v warning -y -i "$source" -map 0:v:0 -an -c:v libx264 -threads 2 \
        -preset veryfast -crf 18 -pix_fmt yuv420p -force_key_frames 'expr:gte(t,n_forced*2)' \
        -bf 0 -fps_mode passthrough -f mp4 "$file.tmp"
    mv "$file.tmp" "$file"
fi
exec ffmpeg -nostdin -loglevel warning -re -stream_loop -1 -i "$file" -map 0:v:0 -c copy \
    -rtsp_transport tcp -f rtsp "rtsp://rtsp:8554/$CAMERA"
