# spotify-downloader
Spotify song downloader using API created by spotifydown.com

The script `src/spotify_dl.py` can be run in interactive mode or CLI mode.  

## Interactive mode

When run without any arguments, interactive mode is used.  The user is prompted for URLs of songs or playlists.  If a playlist is given, the user has the option to download individual songs from that playlist or all of them as well as the ability to see the songs in the playlist prior to making a decision.  The default download directory is the user's `Downloads/` directory, e.g. `C:\Users\[USER]\Downloads\`.  The user is prompted if they want to change the directory prior to downloading the songs.

## CLI mode

```shell
usage: spotify_dl.py [-h] [-u URLS [URLS ...]] [-o OUTPUT] [-c] [-s] [-k CONFIG_FILE] [--debug]

optional arguments:
  -u URLS [URLS ...], --urls URLS [URLS ...]
                        URL(s) of Sptofy songs or playlists to download. If a playlist is given, append "|[TRACK NUMBERS]" to URL to specify which tracks to download. Example:
                        'https://open.spotify.com/playlist/mYpl4YLi5T|1,4,15-' to download the first, fourth, and fifteenth to the end. If not specified, all tracks are downloaded.
                        This argument is required is a CONFIG_FILE isn't specified.
  -o OUTPUT, --output OUTPUT
                        Path to directory where tracks should be downloaded to
  -c, --create-dir      Create the output directory if it does not exist.
  -s, --skip-duplicate-downloads
                        Don't download a song if the file already exists in the output directory.
  -k CONFIG_FILE, --config-file CONFIG_FILE
                        Path to JSON containing download instructions.
  --debug               Debug mode.
```

### Config file

Example of JSON file used to contain download instructions:
```json
[
  {
    "url": "https://open.spotify.com/playlist/mYpl4YLi5T"
  },
  {
    "url": "https://open.spotify.com/playlist/mYpl4YLi5T2",
    "output_dir": "/path/to/dir/",
    "create_dir": true
  },
  {
    "url": "https://open.spotify.com/playlist/mYpl4YLi5T3",
    "skip_duplicate_downloads": true
  }
]
```
