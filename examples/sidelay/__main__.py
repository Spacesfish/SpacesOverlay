"""
Parse the chat on Hypixel to detect players in your party and bedwars lobby

Run from the root dir by `python -m examples.sidelay <path-to-logfile>`
"""

import logging
import queue
import sys
import threading
import time
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Callable, Iterable, Literal, NoReturn, Optional, TextIO

from appdirs import AppDirs

from examples.sidelay.output.overlay import run_overlay
from examples.sidelay.output.printing import print_stats_table
from examples.sidelay.parsing import parse_logline
from examples.sidelay.settings import Settings, get_settings
from examples.sidelay.state import OverlayState, update_state
from examples.sidelay.stats import (
    Stats,
    get_bedwars_stats,
    get_cached_stats,
    set_player_pending,
    sort_stats,
)
from hystatutils.playerdata import HypixelAPIKeyHolder

dirs = AppDirs(appname="hystatutils_overlay")
CONFIG_DIR = Path(dirs.user_config_dir)
DEFAULT_SETTINGS_PATH = CONFIG_DIR / "settings.toml"

logging.basicConfig()
logger = logging.getLogger()

TESTING = False
CLEAR_BETWEEN_DRAWS = True
DOWNLOAD_THREAD_COUNT = 15


def resolve_path(p: str) -> Path:
    return Path(p).resolve()


def get_options() -> Namespace:
    parser = ArgumentParser()

    parser.add_argument(
        "logfile",
        help="Path to launcher_log.txt",
        type=resolve_path,
    )

    parser.add_argument(
        "-s",
        "--settings",
        help="Path to the .toml settings-file",
        type=resolve_path,
        default=DEFAULT_SETTINGS_PATH,
    )

    return parser.parse_args()


class UpdateStateThread(threading.Thread):
    """Thread that reads from the logfile and updates the state"""

    def __init__(
        self,
        state: OverlayState,
        loglines: Iterable[str],
        redraw_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True)  # Don't block the process from exiting
        self.state = state
        self.loglines = loglines
        self.redraw_event = redraw_event

    def run(self) -> None:
        """Read self.loglines and update self.state"""
        try:
            for line in self.loglines:
                event = parse_logline(line)

                if event is None:
                    continue

                with self.state.mutex:
                    redraw = update_state(self.state, event)
                    if redraw:
                        # Tell the main thread we need a redraw
                        self.redraw_event.set()
        except (OSError, ValueError) as e:
            # Catch 'read on closed file' if the main thread exited
            logger.debug(f"Exception caught in state update tread: {e}. Exiting")
            return


class GetStatsThread(threading.Thread):
    """Thread that downloads and stores players' stats to cache"""

    def __init__(
        self,
        requests_queue: queue.Queue[str],
        completed_queue: queue.Queue[str],
        key_holder: HypixelAPIKeyHolder,
    ) -> None:
        super().__init__(daemon=True)  # Don't block the process from exiting
        self.requests_queue = requests_queue
        self.completed_queue = completed_queue
        self.key_holder = key_holder

    def run(self) -> None:
        """Get requested stats from the queue and download them"""
        while True:
            username = self.requests_queue.get()

            # get_bedwars_stats sets the stats cache which will be read from later
            get_bedwars_stats(username, key_holder=self.key_holder)
            self.requests_queue.task_done()

            # Tell the main thread that we downloaded this user's stats
            self.completed_queue.put(username)


def tail_file(f: TextIO) -> Iterable[str]:
    """Iterate over new lines in a file"""
    f.seek(0, 2)
    while True:
        line = f.readline()
        if not line:
            # No new lines -> wait
            time.sleep(0.1)
            continue

        yield line


def fast_forward_state(state: OverlayState, loglines: Iterable[str]) -> None:
    """Process the state changes for each logline without outputting anything"""
    for line in loglines:
        event = parse_logline(line)

        if event is None:
            continue

        update_state(state, event)


def should_redraw(
    state: OverlayState,
    redraw_event: threading.Event,
    completed_stats_queue: queue.Queue[str],
) -> bool:
    """Check if any updates happened since last time that needs a redraw"""
    # Check the work done by the state update and stats download threads
    redraw = False

    # Check if the state update thread has issued any redraws since last time
    with state.mutex:
        if redraw_event.is_set():
            redraw = True
            redraw_event.clear()

    # Check if any of the stats downloaded since last render are still in the lobby
    while True:
        try:
            username = completed_stats_queue.get_nowait()
        except queue.Empty:
            break
        else:
            completed_stats_queue.task_done()
            with state.mutex:
                if username in state.lobby_players:
                    # We just received the stats of a player in the lobby
                    # Redraw the screen in case the stats weren't there last time
                    redraw = True

    return redraw


def prepare_overlay(
    state: OverlayState,
    key_holder: HypixelAPIKeyHolder,
    loglines: Iterable[str],
    thread_count: int,
) -> Callable[[], Optional[list[Stats]]]:
    """
    Set up and return get_stat_list

    get_stat_list returns an updated list of stats of the players in the lobby,
    or None if no updates happened since last call.

    This function spawns threads that perform the state updates and stats downloading
    """

    # Usernames we want the stats of
    requested_stats_queue = queue.Queue[str]()
    # Usernames we have newly downloaded the stats of
    completed_stats_queue = queue.Queue[str]()
    # Redraw requests from state updates
    redraw_event = threading.Event()

    # Spawn thread for updating state
    UpdateStateThread(state=state, loglines=loglines, redraw_event=redraw_event).start()

    # Spawn threads for downloading stats
    for i in range(thread_count):
        GetStatsThread(
            requests_queue=requested_stats_queue,
            completed_queue=completed_stats_queue,
            key_holder=key_holder,
        ).start()

    def get_stat_list() -> Optional[list[Stats]]:
        """
        Get an updated list of stats of the players in the lobby. None if no updates
        """

        redraw = should_redraw(
            state,
            redraw_event=redraw_event,
            completed_stats_queue=completed_stats_queue,
        )

        if not redraw:
            return None

        # Get the cached stats for the players in the lobby
        stats: list[Stats] = []

        with state.mutex:
            for player in state.lobby_players:
                cached_stats = get_cached_stats(player)
                if cached_stats is None:
                    # No query made for this player yet
                    # Start a query and note that a query has been started
                    cached_stats = set_player_pending(player)
                    requested_stats_queue.put(player)
                stats.append(cached_stats)

            sorted_stats = sort_stats(stats, state.party_members)

            return sorted_stats

    return get_stat_list


def process_loglines_to_stdout(
    state: OverlayState,
    key_holder: HypixelAPIKeyHolder,
    loglines: Iterable[str],
    thread_count: int = DOWNLOAD_THREAD_COUNT,
) -> None:
    """Process the state changes for each logline and redraw the screen if neccessary"""
    get_stat_list = prepare_overlay(
        state, key_holder, loglines, thread_count=thread_count
    )

    while True:
        time.sleep(0.1)

        sorted_stats = get_stat_list()

        if sorted_stats is None:
            continue

        with state.mutex:
            print_stats_table(
                sorted_stats=sorted_stats,
                party_members=state.party_members,
                out_of_sync=state.out_of_sync,
                clear_between_draws=CLEAR_BETWEEN_DRAWS,
            )


def process_loglines_to_overlay(
    state: OverlayState,
    key_holder: HypixelAPIKeyHolder,
    loglines: Iterable[str],
    output_to_console: bool,
    thread_count: int = DOWNLOAD_THREAD_COUNT,
) -> None:
    """Process the state changes for each logline and output to an overlay"""
    get_stat_list = prepare_overlay(state, key_holder, loglines, thread_count)

    if output_to_console:
        # Output to console every time we get a new stats list
        original_get_stat_list = get_stat_list

        def get_stat_list() -> Optional[list[Stats]]:
            sorted_stats = original_get_stat_list()

            if sorted_stats is not None:
                with state.mutex:
                    print_stats_table(
                        sorted_stats=sorted_stats,
                        party_members=state.party_members,
                        out_of_sync=state.out_of_sync,
                        clear_between_draws=CLEAR_BETWEEN_DRAWS,
                    )

            return sorted_stats

    run_overlay(state, get_stat_list)


def watch_from_logfile(
    logpath: str, output: Literal["stdout", "overlay"], settings: Settings
) -> None:
    """Use the overlay on an active logfile"""

    key_holder = HypixelAPIKeyHolder(settings.hypixel_api_key)

    def set_api_key(new_key: str) -> None:
        """Update the API key that the download threads use"""
        # TODO: Potentially invalidate the entire/some parts of the stats cache
        key_holder.key = new_key
        settings.hypixel_api_key = new_key
        settings.flush_to_disk()

    state = OverlayState(
        lobby_players=set(), party_members=set(), set_api_key=set_api_key
    )

    with open(logpath, "r", encoding="utf8") as logfile:
        # Process the entire logfile to get current player as well as potential
        # current party/lobby
        fast_forward_state(state, logfile.readlines())

        loglines = tail_file(logfile)

        # Process the rest of the loglines as they come in
        if output == "stdout":
            process_loglines_to_stdout(state, key_holder, loglines)
        else:
            process_loglines_to_overlay(
                state, key_holder, loglines, output_to_console=True
            )


def test() -> None:
    """Test the implementation on a static logfile"""
    global TESTING, CLEAR_BETWEEN_DRAWS

    TESTING = True
    CLEAR_BETWEEN_DRAWS = False

    logger.setLevel(logging.DEBUG)

    assert len(sys.argv) >= 3
    output = "overlay" if len(sys.argv) >= 4 and sys.argv[3] == "overlay" else "stdout"

    state = OverlayState(
        lobby_players=set(), party_members=set(), set_api_key=lambda x: None
    )
    key_holder = HypixelAPIKeyHolder("")

    with open(sys.argv[2], "r", encoding="utf8") as logfile:
        loglines = logfile
        if output == "overlay":
            from itertools import chain, islice, repeat

            loglines_with_pause = chain(islice(repeat(""), 500), loglines, repeat(""))
            process_loglines_to_overlay(
                state, key_holder, loglines_with_pause, output_to_console=True
            )
        else:
            process_loglines_to_stdout(state, key_holder, loglines)


if __name__ == "__main__":
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Failed creating settings directory! '{e}'", file=sys.stderr)
        logger.error(f"Failed creating settings directory! '{e}'")
        sys.exit(1)

    if len(sys.argv) == 2 and sys.argv[1] == "test":
        test()
    else:
        options = get_options()

        def get_api_key() -> NoReturn:
            print(
                "Please provide a Hypixel API key in the settings file", file=sys.stderr
            )
            sys.exit(1)

        settings = get_settings(options.settings, get_api_key)
        watch_from_logfile(str(options.logfile.resolve()), "overlay", settings=settings)
