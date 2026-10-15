#!/bin/bash
# Launch the python app in the background so X11 display is ready
python3 /app/pdf_studio.py &

# Hand off to the original webtop initialization process
exec /init
