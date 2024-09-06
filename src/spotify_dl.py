import json
import re
import requests
import signal
import sys
import traceback
from argparse import ArgumentParser
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import sleep

# Want to figure out how to do this without a third party module
import eyed3
from eyed3.id3 import ID3_V2_3
from eyed3.id3.frames import ImageFrame

# Suppress warnings about CRC fail for cover art
import logging
logging.getLogger('eyed3.mp3.headers').warning = logging.debug


# Cheeky Ctrl+C handler
signal.signal(signal.SIGINT, lambda sig, frame : print('\n\nInterrupt received. Exiting.\n') or sys.exit(0))

DOWNLOADER_URL = "https://api.spotifydown.com"
# Clean browser heads for API
DOWNLOADER_HEADERS = {
    'Host': 'api.spotifydown.com',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip',
    'Referer': 'https://spotifydown.com/',
    'Origin': 'https://spotifydown.com',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
    'Sec-GPC': '1',
    'TE': 'trailers'
}

MULTI_TRACK_INPUT_URL_TRACK_NUMS_RE = re.compile(r'^https?:\/\/open\.spotify\.com\/(album|playlist)\/[\w]+(?:\?[\w=%-]*|)\|(?P<track_nums>.*)$')

# In interactive mode, user is prompted upon first duplicate encountered
# Otherwise, set via CLI arg
skip_duplicate_downloads = True
skip_duplicate_downloads_prompted = True


@dataclass(frozen=True, eq=True)
class SpotifySong:
    title: str
    artist: str
    album: str
    id: str
    url = f"https://open.spotify.com/track/{id}"


def parse_cfg(cfg_path: Path) -> ConfigParser:
    parser = ConfigParser()
    parser.read(cfg_path)

    return parser


def assemble_track_custom_title(
    title: str,
    template: str,
    artist: str = "",
    track_num: int = 1
) -> str:
    if not template:
        template = r"{title} - {artist}"
    else:
        # validate given template
        allowed_vars = ["title", "artist", "track_num"]
        for detected_var in re.findall(r"{(\w+)}", template):
            if detected_var not in allowed_vars:
                raise ValueError(
                    "Variable in filename template of not one of "
                    f"{', '.join(map(repr, allowed_vars))}. Found: '{detected_var}'"
                )

    template = template.replace(r"{track_num}", str(track_num)) \
        .replace(r"{title}", title) \
        .replace(r"{artist}", artist)

    return template


def _call_downloader_api(
    endpoint: str,
    method: str = 'GET',
    headers=DOWNLOADER_HEADERS,
    **kwargs
) -> requests.Response:
    _map = {
        'GET': requests.get,
        'POST': requests.post
    }

    if method not in _map:
        raise ValueError

    try:
        resp = _map[method](DOWNLOADER_URL + endpoint, headers=headers, **kwargs)
    except Exception as exc:
        raise RuntimeError("ERROR: ", exc)

    return resp


def validate_config_file(config_file: Path) -> list:
    # TODO: validate entries
    with open(config_file) as config_fp:
        loaded_config = json.load(config_fp)

    return loaded_config


def get_track_data(track_id: str):
    resp = _call_downloader_api(f"/download/{track_id}")

    resp_json = resp.json()

    if not resp_json['success']:
        # print("[!] Bad URL. No song found.")
        resp_json = {}

    return resp_json


def get_multi_track_data(entity_id: str, entity_type: str):
    metadata_resp = _call_downloader_api(f"/metadata/{entity_type}/{entity_id}").json()

    # For paginated response
    track_list = []

    tracks_resp = _call_downloader_api(f"/trackList/{entity_type}/{entity_id}").json()

    if not tracks_resp.get('trackList'):
        return {}

    track_list.extend(tracks_resp['trackList'])

    while next_offset := tracks_resp.get('nextOffset'):
        tracks_resp = _call_downloader_api(f"/trackList/{entity_type}/{entity_id}?offset={next_offset}").json()
        track_list.extend(tracks_resp['trackList'])

    if not metadata_resp['success']:
        return {}

    return {
        **metadata_resp,
        'trackList': [
            SpotifySong(
                title=track['title'],
                artist=track['artists'],
                album=track['album'] if entity_type == "playlist" else metadata_resp['title'],
                id=track['id']
            )
            for track in track_list
        ]
    }


def get_spotify_playlist(playlist_id: str, token: str):
    # GET to playlist URL can get first 30 songs only
    # soup.find_all('meta', content=re.compile("https://open.spotify.com/track/\w+"))

    playlist_resp = requests.get(
        f'https://api.spotify.com/v1/playlists/{playlist_id}',
        headers={'Authorization': f"Bearer {token}"}
    )

    playlist = playlist_resp.json()

    tracks_list = [
        SpotifySong(
            title=track['track']['name'],
            artist=', '.join(artist['name'] for artist in track['track']['artists']),
            album=track['track']['album']['name'],
            id=track['track']['id']
        )
        for track in playlist['tracks']['items']
    ]

    return playlist['name'], playlist['owner']['display_name'], tracks_list


def get_tracks_to_download(interactive: bool, filename_template, cli_arg_urls: list = None) -> list:
    tracks_to_dl = []

    if interactive:
        prompt = "Enter URL for Spotify track/playlist, or leave blank and press [ENTER] when done.\n---URL: "

        while url := input(prompt):
            track_id_title_tuple_list = process_input_url(url, filename_template, interactive)

            if not track_id_title_tuple_list:
                continue

            tracks_to_dl.extend(track_id_title_tuple_list)

    else:
        for url in cli_arg_urls:
            track_id_title_tuple_list = process_input_url(url, filename_template, interactive)

            if not track_id_title_tuple_list:
                continue

            tracks_to_dl.extend(track_id_title_tuple_list)

    return tracks_to_dl


def set_output_dir(interactive: bool, cli_arg_output_dir: Path, cli_arg_create_dir: bool = None) -> None:
    default_output_dir = 'C:/Users/tdv/Desktop/Music'

    if (spotify_dl_cfg_path := Path.home()/".spotify_dl.cfg").is_file():
        spotify_dl_cfg = parse_cfg(spotify_dl_cfg_path)
        default_output_dir = spotify_dl_cfg.get("Settings", "default_download_location", fallback=default_output_dir)

    if interactive:
        output_dir = Path(default_output_dir)
        print(f"Downloads will go to {output_dir}.  If you would like to change, enter the location or press [ENTER]")

        if other_dir := input("(New download location?) "):
            output_dir = Path(other_dir)

        while not output_dir.is_dir():
            mkdir_inp = input(f"The directory '{output_dir.absolute()}' does not exist.  Would you like to create it? [y/n]: ")
            if mkdir_inp.lower() == 'y':
                output_dir.mkdir(parents=True)
            else:
                output_dir = Path(input("\nNew download location: "))

    else:
        output_dir = cli_arg_output_dir

        if not output_dir.is_dir():
            if cli_arg_create_dir:
                output_dir.mkdir(parents=True)
            else:
                raise ValueError(
                    f"Specified directory '{output_dir}' is not a valid directory."
                )

    return output_dir


def track_num_inp_to_ind(given_inp: str, list_len: int) -> list:
    indexes_or_slices = []
    # Remove whitespace
    no_ws = re.sub(r'\s', '', given_inp)

    for item in no_ws.split(','):

        # TODO: allow negative to get last n songs?

        if item.isnumeric(): # ensure the user inputs a valid number in the playlist range
            if not (1 <= int(item) <= list_len):
                print(f"Track number {item} does not exist.  Valid numbers are 1 - {list_len}")
                continue
            # Subtract one for indexing
            indexes_or_slices.append(str(int(item) - 1))

        elif '-' in item:
            start, end = item.split('-')
            if not start:
                # '-3' --> :3 since that gets the first three tracks, 0, 1, and 2
                indexes_or_slices.append(f":{end}")
            elif not end:
                indexes_or_slices.append(f"{int(start) - 1}:")
            else:
                indexes_or_slices.append(f"{int(start) - 1}:{end}")

        elif item == '*':
            indexes_or_slices.append(':')

        else:
            print(f'    [!] Invalid input: {item}')

    if not indexes_or_slices:
        print(f"    [!] No valid input received: '{given_inp}'. Try again.")

    return indexes_or_slices


def get_track_nums_input(tracks: list, entity_type: str) -> str:
    track_numbers_inp = None

    while not track_numbers_inp:
        track_numbers_inp = input('\n'
            f"    Enter 'show' to list the {entity_type} tracks, the track numbers to download, or '*' to download all:\n"
            "      Example: '1, 4, 15-' to download the first, fourth, and fifteenth to the end\n"
            "  > "
        )

        if 'show' in track_numbers_inp.lower():
            print(
                '\n    ',
                '\n    '.join(f"{ind + 1:>4}| {track.title} - {track.artist}" for ind, track in enumerate(tracks)),
                '\n',
                sep=''
            )
            track_numbers_inp = None

    return track_numbers_inp


def process_input_url(url: str, filename_template: str, interactive: bool) -> list:
    track_id_title_tuples = []

    if "/track/" in url:
        track_resp_json = get_track_data(track_id=url.split('/')[-1].split('?')[0])

        if not track_resp_json:
            print(f"\t[!] Song not found{f' at {url}' if not interactive else ''}.")
            return []

        track_title = assemble_track_custom_title(
            title=track_resp_json['metadata']['title'],
            artist=track_resp_json['metadata']['artists'],
            track_num=0,
            template=filename_template
        )

        print(f"\t{track_title}")

        track_id_title_tuples.append((track_resp_json['metadata']['id'], track_title))

    elif "/playlist/" in url or "/album/" in url:
        entity_id = url.split('/')[-1].split('?')[0].split('|')[0]

        if "/playlist/" in url:
            entity_type = "playlist"
        else:
            entity_type = "album"

        # playlist_name, playlist_creator, playlist_tracks = get_spotify_playlist(playlist_id, token)
        multi_track_resp_json = get_multi_track_data(entity_id, entity_type)

        if not multi_track_resp_json:
            print(
                f"\t[!] {entity_type.capitalize()} not found{f' at {url}' if not interactive else ''}"
                f"{' or it is set to Private' if entity_type == 'playlist' else ''}."
            )
            return []

        # print(f"\t{playlist_name} - {playlist_creator} ({len(playlist_tracks)} tracks)")
        print(f"\t{multi_track_resp_json['title']} - {multi_track_resp_json['artists']} ({len(multi_track_resp_json['trackList'])} tracks)")

        album_or_playlist_tracks = multi_track_resp_json['trackList']

        if interactive:
            print("Downloading all tracks.")
            track_numbers_inp = '*'
            indexes_or_slices = track_num_inp_to_ind(track_numbers_inp, list_len=len(album_or_playlist_tracks))

        else:
            if specified_track_nums := MULTI_TRACK_INPUT_URL_TRACK_NUMS_RE.match(url):
                track_numbers_inp = specified_track_nums.group('track_nums')
            else:
                # Default to downloading whole playlist/album
                track_numbers_inp = '*'

            indexes_or_slices = track_num_inp_to_ind(track_numbers_inp, list_len=len(album_or_playlist_tracks))

            if not indexes_or_slices:
                raise ValueError(
                    f"Invalid track number indentifer(s) given: '{specified_track_nums}'"
                )

        # Process input given for which tracks to download
        tracks_to_dl = []
        for index_or_slice in indexes_or_slices:

            if index_or_slice.isnumeric():
                tracks_to_dl.append(album_or_playlist_tracks[int(index_or_slice)])
            else:
                tracks_to_dl.extend(
                    eval(f"album_or_playlist_tracks[{index_or_slice}]")
                )

        for track in sorted(tracks_to_dl, key=album_or_playlist_tracks.index):

            track_num = album_or_playlist_tracks.index(track) + 1

            track_title = assemble_track_custom_title(
                title=track.title,
                artist=track.artist,
                track_num=track_num,
                template=filename_template
            )

            print(f"\t{track_num:>4}| {track_title}")

            track_id_title_tuples.append((track.id, track_title))

        print("Press Enter to download all tracks.")
    else:
        print(f"\t[!] Invalid URL{f' -- {url}' if not interactive else ''}.")
        return []

    return track_id_title_tuples


def download_track(track_id, track_title, dest_dir: Path, interactive: bool = False, skip_duplicates: bool = False):
    track_filename = re.sub(r'[<>:"/\|?*]', '_', f"{track_title}.mp3")

    global skip_duplicate_downloads
    global skip_duplicate_downloads_prompted

    if (dest_dir/track_filename).exists():
        if skip_duplicates or skip_duplicate_downloads:
            print(f"Skipping download for '{track_title}'...")
            return

        if interactive and not skip_duplicate_downloads_prompted:
            dup_song_inp = input(
                f"The song '{track_title}' was already downloaded to {dest_dir.absolute()}.\n"
                "  Would you like to download it again? [y/N]: "
            )

            if skip_this_dl := (not dup_song_inp or dup_song_inp.lower().startswith('n')):
                print("\nSkipping download.\n")
                # Prompt user if we haven't yet before skipping this one

            if not skip_duplicate_downloads_prompted:
                dup_all_inp = input(
                    "  Would you like to re-download songs that have already been downloaded? [y/N]: "
                )

                if not dup_all_inp or dup_all_inp.lower().startswith('n'):
                    skip_duplicate_downloads = True
                    print("\nSkipping duplicate downloads.\n")
                else:
                    skip_duplicate_downloads = False
                    print("\nRe-downloading all tracks.\n")

                skip_duplicate_downloads_prompted = True

            if skip_this_dl:
                return

    print(f"Downloading: '{track_title}'...")

    # Grab a fresh download link since the one was got may have expired
    resp_json = get_track_data(track_id)

    # Clean browser heads for API
    hdrs = {
        #'Host': 'cdn[#].tik.live', # <-- set this below
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip',
        'Referer': 'https://spotifydown.com/',
        'Origin': 'https://spotifydown.com',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'cross-site',
        'Sec-GPC': '1'
    }

    if 'link' not in resp_json or 'metadata' not in resp_json:
        print("\tDownload failed.")
        raise RuntimeError(
            f"Bad response for track '{track_title}' ({track_id}): {resp_json}"
        )

    # For audio
    hdrs['Host'] = resp_json['link'].split('/')[2]
    audio_dl_resp = requests.get(resp_json['link'], headers=hdrs)

    if not audio_dl_resp.ok:
        raise RuntimeError(
            f"Bad download response for track '{track_title}' ({track_id}): {audio_dl_resp.content}"
        )

    with open(dest_dir/track_filename, 'wb') as track_mp3_fp:
        track_mp3_fp.write(audio_dl_resp.content)

    # For cover art
    if cover_art_url := resp_json['metadata'].get('cover'):
        hdrs['Host'] = cover_art_url.split('/')[2]
        cover_resp = requests.get(cover_art_url,headers=hdrs)

        mp3_file = eyed3.load(dest_dir/track_filename)
        if (mp3_file.tag == None):
            mp3_file.initTag()

        mp3_file.tag.images.set(ImageFrame.FRONT_COVER, cover_resp.content, 'image/jpeg')
        mp3_file.tag.album = resp_json['metadata']['album']
        mp3_file.tag.recording_date = resp_json['metadata']['releaseDate']

        # default version lets album art show up in Serato
        #mp3_file.tag.save()
        # version fixes FRONT_COVER not showing up in windows explorer
        mp3_file.tag.save(version=ID3_V2_3)

    # prevent API throttling
    sleep(0.1)

    print("\tDone.")


def download_all_tracks(
    tracks_to_dl: list,
    output_dir: Path,
    interactive: bool,
    skip_duplicate_downloads: bool,
    debug_mode: bool = False
) -> list:
    print(f"\nDownloading to '{output_dir.absolute()}'.\n")

    print('-' * 32)

    tracks = list(dict.fromkeys(tracks_to_dl))
    broken_tracks = []

    for idx, (track_id, track_title) in enumerate(tracks, start=1):
        print(f"[{idx:>3}/{len(tracks):>3}]", end=' ')
        try:
            download_track(track_id, track_title, output_dir, interactive, skip_duplicate_downloads)
        except Exception as exc:
            broken_tracks.append((track_id, track_title, output_dir))
            if debug_mode:
                with open('.spotify_dl_err.txt', 'a') as debug_fp:
                    debug_fp.write(f"{datetime.now()} | {exc} :: {traceback.format_exc()}\n\n")

    print("\nAll done.\n")
    if broken_tracks:
        print("[!] Some tracks failed to download.")

    return broken_tracks


def spotify_downloader(
    interactive: bool,
    urls: list = None,
    output_dir: Path = None,
    create_dir: bool = None,
    skip_duplicate_downloads: bool = None,
    debug_mode: bool = None,
    filename_template: str = r"{title} - {artist}"
):
    loop_prompt = True
    
    broken_tracks = []
    while loop_prompt and (tracks_to_dl := get_tracks_to_download(interactive, filename_template, urls)):

        print(f"\nNumber of tracks to download: {len(tracks_to_dl)}\n")

        output_dir = set_output_dir(interactive, output_dir, create_dir)

        broken_tracks.extend(
            download_all_tracks(
                tracks_to_dl,
                output_dir,
                interactive,
                skip_duplicate_downloads,
                debug_mode
            )
        )
        if not interactive:
            loop_prompt = False

    return broken_tracks


def parse_args():
    parser = ArgumentParser()

    parser.add_argument(
        '-u',
        '--urls',
        nargs='+',
        help="URL(s) of Sptofy songs or playlists to download.  "
            "If a playlist is given, append \"|[TRACK NUMBERS]\" to URL to specify which tracks to download. "
            "Example: 'https://open.spotify.com/playlist/mYpl4YLi5T|1,4,15-' to download the first, fourth, "
            "and fifteenth to the end. If not specified, all tracks are downloaded."
    )
    parser.add_argument(
        '-f',
        '--filename',
        type=str,
        default=r"{title} - {artist}",
        help="Specify custom filename."
    )
    parser.add_argument(
        '-o',
        '--output',
        type=Path,
        default=Path.home()/"Downloads",
        help="Path to directory where tracks should be downloaded to"
    )
    parser.add_argument(
        '-c',
        '--create-dir',
        action='store_true',
        help="Create the output directory if it does not exist."
    )
    parser.add_argument(
        '-s',
        '--skip-duplicate-downloads',
        action='store_true',
        default=False,
        help="Don't download a song if the file already exists in the output directory."
    )
    parser.add_argument(
        '-k',
        '--config-file',
        type=Path,
        help="Path to JSON containing download instructions."
    )
    parser.add_argument(
        '--retry-failed-downloads',
        type=int,
        help="Number of times to retry failed downloads."
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        default=False,
        help="Debug mode."
    )

    return parser.parse_args()


def main():
    print('', '=' * 48, '||          Spotify Song Downloader           ||', '=' * 48, sep='\n', end='\n\n')

    # # Grab token anyway
    # token_resp = requests.get("https://open.spotify.com/get_access_token")
    # # clientId, accessToken
    # token = token_resp.json()['accessToken']

    # No given args
    if len(sys.argv) == 1:
        # Interactive mode
        interactive = True

        broken_tracks = spotify_downloader(
            interactive=interactive,
            output_dir=None,
            urls=None,
            create_dir=None,
            debug_mode=None
        )

    else:
        # CLI mode
        interactive = False

        args = parse_args()

        if not (config_file := args.config_file):

            if not (urls := args.urls):
                raise ValueError(
                    "The '-u'/'--urls' argument must be "
                    "supplied if not using a config file"
                )

            broken_tracks = spotify_downloader(
                interactive=interactive,
                output_dir=args.output,
                urls=urls,
                create_dir=args.create_dir,
                skip_duplicate_downloads=args.skip_duplicate_downloads,
                debug_mode=args.debug,
                filename_template=args.filename
            )

        else:
            loaded_config = validate_config_file(config_file)

            broken_tracks = []

            for entry in loaded_config:
                broken_tracks.extend(
                    spotify_downloader(
                        interactive=interactive,
                        output_dir=Path(entry['output_dir']) if 'output_dir' in entry else Path.home()/"Downloads",
                        urls=[entry['url']],
                        create_dir=entry.get('create_dir'),
                        skip_duplicate_downloads=entry.get('skip_duplicate_downloads'),
                        debug_mode=args.debug,
                        filename_template=entry.get('filename_template')
                    )
                )

    if broken_tracks:
        nl = '\n'
        print(
            "\n[!] The following tracks could not be downloaded:\n"
            f"  * {f'{nl}  * '.join(t_title for t_id, t_title, out_dir in broken_tracks)}\n"
        )

        if not interactive:
            num_retries = args.retry_failed_downloads or 0
        else:
            print("Retrying...")
            num_retries = 5
            for i in range(num_retries):
                print(f"Attempt {i + 1} of {num_retries}") 
                for track_id, track_title, output_dir in broken_tracks.copy():
                    try:
                        download_track(track_id, track_title, output_dir)
                    except Exception:
                        continue
                    else:
                        broken_tracks.remove((track_id, track_title, output_dir))
                sleep(1)

        if interactive:
            input("\nPress [ENTER] to exit.\n")

    # Give a chance to see the messages if running via executable
    sleep(1)
    print("\nExiting...\n")
    sleep(3)


if __name__ == '__main__':
    main()
