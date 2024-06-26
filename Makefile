spotify_dl:
	echo "\n*Building with PyInstaller*\n"
	pyinstaller src/spotify_dl.py --onefile
	echo "\nDone"
