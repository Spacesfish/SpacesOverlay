"""
Search for /who responses in the logfile and print the found users' stats

Run from the examples dir by `python -m sidelay <path-to-logfile>`

Example string printed to logfile when typing /who:
'[Info: 2021-10-29 15:29:33.059151572: GameCallbacks.cpp(162)] Game/net.minecraft.client.gui.GuiNewChat (Client thread) Info [CHAT] ONLINE: The_TOXIC__, T_T0xic, maskaom, NomexxD, Killerluise, Fruce_, 3Bitek, OhCradle, DanceMonky, Sweeetnesss, jbzn, squashypeas, Skydeaf, serocore'  # noqa: E501
"""

import logging
import sys
import time
from pathlib import Path
from typing import Iterable, TextIO

from examples.sidelay.parsing import parse_logline
from examples.sidelay.printing import print_stats_table
from examples.sidelay.state import OverlayState, update_state
from examples.sidelay.stats import NickedPlayer, Stats, get_bedwars_stats
from hystatutils.utils import read_key

logging.basicConfig()
logger = logging.getLogger()

api_key = read_key(Path(sys.path[0]) / "api_key")

TESTING = False
CLEAR_BETWEEN_DRAWS = True


def tail_file(f: TextIO) -> Iterable[str]:
    """Iterate over new lines in a file"""
    f.seek(0, 2)
    while True:
        line = f.readline()
        if not line:
            # No new lines -> wait
            time.sleep(0.01)
            continue

        yield line


def process_loglines(loglines: Iterable[str]) -> None:
    """Process the state changes for each logline and redraw the screen if neccessary"""
    state = OverlayState(lobby_players=set(), party_members=set())

    for line in loglines:
        event = parse_logline(line)

        if event is not None:
            redraw = update_state(state, event)
        else:
            redraw = False

        if redraw:
            logger.info(f"Party = {', '.join(state.party_members)}")
            logger.info(f"Lobby = {', '.join(state.lobby_players)}")

            stats: list[Stats]
            if TESTING:
                # No api requests in testing
                stats = [
                    NickedPlayer(username=player) for player in state.lobby_players
                ]
            else:
                stats = [
                    get_bedwars_stats(player, api_key=api_key)
                    for player in state.lobby_players
                ]

            print_stats_table(
                stats=stats,
                party_members=state.party_members,
                out_of_sync=state.out_of_sync,
                clear_between_draws=CLEAR_BETWEEN_DRAWS,
            )


def watch_from_logfile(logpath: str) -> None:
    """Use the overlay on an active logfile"""
    with open(logpath, "r") as logfile:
        loglines = tail_file(logfile)
        process_loglines(loglines)


def test() -> None:
    """Test the implementation on a static logfile"""
    global TESTING, CLEAR_BETWEEN_DRAWS

    TESTING = True
    CLEAR_BETWEEN_DRAWS = False

    logger.setLevel(logging.DEBUG)

    assert len(sys.argv) >= 3

    with open(sys.argv[2], "r") as logfile:
        loglines = logfile
        process_loglines(loglines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Must provide a path to the logfile!", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "test":
        test()
    else:
        watch_from_logfile(sys.argv[1])