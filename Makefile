spotify_dl:
	echo "\n*Building with PyInstaller*\n"
	python -m venv env
	./env/Scripts/activate
	pip install -r requirements.txt
	./env/Scripts/deactivate
	pyinstaller src/spotify_dl.py --onefile --paths ./env/Lib/site-packages
	echo "\nDone"
