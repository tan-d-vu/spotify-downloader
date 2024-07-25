Download the `spotify_dl.exe` in this directory.  The one inside of `spotify_dl/` requires the `_internal/` dir as well, so you're better off using this one here.

Windows is not going to like the fact that this is an unknown .exe, but there is no nonsense going on here.  The .exe is just the output of `pyinstaller src/spotify_dl.py --onefile --paths [site-packages of venv with requirements.txt installed]`.
