import logging
import queue
import threading
from typing import Callable, Iterable, Optional

import examples.overlay.antisniper_api as antisniper_api
from examples.overlay.nick_database import NickDatabase
from examples.overlay.parsing import parse_logline
from examples.overlay.state import OverlayState, update_state
from examples.overlay.stats import (
    Stats,
    get_bedwars_stats,
    get_cached_stats,
    set_player_pending,
    sort_stats,
)
from prism.playerdata import HypixelAPIKeyHolder

logger = logging.getLogger(__name__)


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
            logger.debug(f"Exception caught in state update thread: {e}. Exiting")
            return
        except Exception:
            logger.exception("Exception caught in state update thread. Exiting")
            return


class GetStatsThread(threading.Thread):
    """Thread that downloads and stores players' stats to cache"""

    def __init__(
        self,
        requests_queue: queue.Queue[str],
        completed_queue: queue.Queue[str],
        hypixel_key_holder: HypixelAPIKeyHolder,
        denick: Callable[[str], Optional[str]],
    ) -> None:
        super().__init__(daemon=True)  # Don't block the process from exiting
        self.requests_queue = requests_queue
        self.completed_queue = completed_queue
        self.hypixel_key_holder = hypixel_key_holder
        self.denick = denick

    def run(self) -> None:
        """Get requested stats from the queue and download them"""
        try:
            while True:
                username = self.requests_queue.get()

                # get_bedwars_stats sets the stats cache which will be read from later
                get_bedwars_stats(
                    username, key_holder=self.hypixel_key_holder, denick=self.denick
                )
                self.requests_queue.task_done()

                # Tell the main thread that we downloaded this user's stats
                self.completed_queue.put(username)
        except Exception:
            logger.exception("Exception caught in stats thread. Exiting")
            return


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
    hypixel_key_holder: HypixelAPIKeyHolder,
    nick_database: NickDatabase,
    loglines: Iterable[str],
    thread_count: int,
    antisniper_key_holder: Optional[antisniper_api.AntiSniperAPIKeyHolder],
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

    def denick(nick: str) -> Optional[str]:
        """Try denicking via the antisniper API, fallback to dict"""
        uuid = None

        if antisniper_key_holder is not None:
            uuid = antisniper_api.denick(nick, key_holder=antisniper_key_holder)

            if uuid is not None:
                logger.debug(f"Denicked with api {nick} -> {uuid}")

        if uuid is None:
            uuid = nick_database.get(nick)

            if uuid is not None:
                logger.debug(f"Denicked with database {nick} -> {uuid}")

        return uuid

    # Spawn thread for updating state
    UpdateStateThread(state=state, loglines=loglines, redraw_event=redraw_event).start()

    # Spawn threads for downloading stats
    for i in range(thread_count):
        GetStatsThread(
            requests_queue=requested_stats_queue,
            completed_queue=completed_stats_queue,
            hypixel_key_holder=hypixel_key_holder,
            denick=denick,
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