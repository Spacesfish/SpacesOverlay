import queue
import threading
import unittest.mock
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

import pytest

from examples.overlay.behaviour import (
    fast_forward_state,
    get_stats_and_winstreak,
    process_loglines,
    set_hypixel_api_key,
    set_nickname,
    should_redraw,
)
from examples.overlay.controller import OverlayController
from examples.overlay.player import MISSING_WINSTREAKS
from tests.examples.overlay import test_get_stats
from tests.examples.overlay.utils import MockedController, create_state, make_winstreaks

USERNAME = "MyIGN"
NICK = "AmazingNick"
UUID = "MyUUID"


def set_known_nicks(known_nicks: dict[str, str], controller: OverlayController) -> None:
    """Update the settings and nickdatabase with the uuid->nick mapping"""
    for uuid, nick in known_nicks.items():
        controller.settings.known_nicks[nick] = {"uuid": uuid, "comment": ""}
        controller.nick_database.default_database[nick] = uuid


KNOWN_NICKS: tuple[dict[str, str], ...] = (
    {},  # No previous known nick
    {"someotheruuid": "randomnick"},  # No prev. known + known for other player
    {UUID: "randomnick"},  # Known different
    {UUID: NICK},  # Known same as new (when setting)
    {UUID: "randomnick", "someotheruuid": "randomnick2"},
)


@pytest.mark.parametrize("known_nicks", KNOWN_NICKS)
def test_set_nickname(known_nicks: dict[str, str]) -> None:
    """Assert that set_nickname works when setting a nick"""
    controller = MockedController(get_uuid=lambda username: UUID)
    controller.player_cache.uncache_player = unittest.mock.MagicMock()  # type: ignore

    set_known_nicks(known_nicks, controller)

    set_nickname(nick=NICK, username=USERNAME, controller=controller)

    # Known nicks updated
    for uuid, nick in known_nicks.items():
        if uuid == UUID and nick != NICK:
            # The player's nick is updated so the old entry should be gone
            assert controller.settings.known_nicks.get(nick, None) is None
            assert controller.nick_database.get(nick) is None
        else:
            # Other players should not be affected
            controller.settings.known_nicks[nick] = {"uuid": uuid, "comment": ""}
            controller.nick_database.default_database[nick] = uuid

    # Nick updated in settings
    assert controller.settings.known_nicks.get(NICK, None) == {
        "uuid": UUID,
        # Comment is kept if the nick is updated
        "comment": USERNAME if UUID not in known_nicks else "",
    }

    # Settings stored
    assert controller._stored_settings == controller.settings

    # Nick updated in database
    assert controller.nick_database.get(NICK) == UUID

    # Cache dropped for new nick
    assert controller.player_cache.uncache_player.called_with(NICK)

    # Cache dropped for old nick
    if UUID in known_nicks:
        assert controller.player_cache.uncache_player.called_with(known_nicks[UUID])


@pytest.mark.parametrize("explicit", (False, True))
@pytest.mark.parametrize("known_nicks", KNOWN_NICKS)
def test_unset_nickname(known_nicks: dict[str, str], explicit: bool) -> None:
    """
    Assert that set_nickname works when unsetting a nick

    Unsetting is either explicit with username=None, or when the uuid of the player
    can't be found
    """
    controller = MockedController(get_uuid=lambda username: UUID if explicit else None)
    controller.player_cache.uncache_player = unittest.mock.MagicMock()  # type: ignore

    set_known_nicks(known_nicks, controller)

    set_nickname(
        nick=NICK, username=None if explicit else USERNAME, controller=controller
    )

    # Nick updated in settings
    assert controller.settings.known_nicks.get(NICK, None) is None

    # Settings stored
    assert controller._stored_settings == controller.settings

    # Nick updated in database
    assert controller.nick_database.get(NICK) is None

    # Cache dropped for old nick
    if UUID in known_nicks:
        assert controller.player_cache.uncache_player.called_with(known_nicks[UUID])


def test_process_event_set_api_key() -> None:
    """Assert that set_hypixel_api_key is called when NewAPIKeyEvent is received"""
    NEW_KEY = "my-new-key"

    controller = MockedController(hypixel_api_key="invalid-key", api_key_invalid=True)
    controller.player_cache.clear_cache = unittest.mock.MagicMock()  # type: ignore

    set_hypixel_api_key(NEW_KEY, controller)

    # Key and key invalid updated
    assert controller.hypixel_api_key == NEW_KEY
    assert not controller.api_key_invalid

    # Settings updated and stored
    assert controller.settings.hypixel_api_key == NEW_KEY
    assert controller._stored_settings == controller.settings

    # Player cache cleared
    controller.player_cache.clear_cache.assert_called()


CHAT = "[Info: 2021-11-29 22:17:40.417869567: GameCallbacks.cpp(162)] Game/net.minecraft.client.gui.GuiNewChat (Client thread) Info [CHAT] "  # noqa: E501
INFO = "[Info: 2021-11-29 23:26:26.372869411: GameCallbacks.cpp(162)] Game/net.minecraft.client.Minecraft (Client thread) Info "  # noqa: E501


@pytest.mark.parametrize(
    "initial_controller, loglines, target_controller",
    (
        (
            MockedController(state=create_state(own_username=None)),
            (
                f"{INFO}Setting user: Me",
                f"{CHAT}Party Moderators: Player1 ● [MVP+] Player2 ● ",
                f"{CHAT}Player1 has joined (1/16)!",
                f"{CHAT}Player2 has joined (2/16)!",
                f"{CHAT}Me has joined (3/16)!",
                f"{CHAT}Someone has joined (4/16)!",
                f"{CHAT}[MVP+] Player1: hows ur day?",
            ),
            MockedController(
                state=create_state(
                    own_username="Me",
                    party_members={"Me", "Player1", "Player2"},
                    lobby_players={"Me", "Player1", "Player2", "Someone"},
                    in_queue=True,
                )
            ),
        ),
    ),
)
def test_fast_forward_state(
    initial_controller: OverlayController,
    loglines: Iterable[str],
    target_controller: OverlayController,
) -> None:
    fast_forward_state(initial_controller, loglines)

    new_controller = initial_controller
    assert new_controller == target_controller


@pytest.mark.parametrize(
    "redraw_event_set, completed_stats, result",
    (
        (False, (), False),
        (False, ("Random1", "Random2"), False),
        (True, (), True),
        (False, ("Random1", "Random2", "Me"), True),
        (True, ("Player1", "Random2", "Me"), True),
    ),
)
def test_should_redraw(
    redraw_event_set: bool, completed_stats: tuple[str], result: bool
) -> None:
    controller = MockedController(
        state=create_state(
            own_username="Me",
            lobby_players={"Me", "Player1", "Player2"},
            in_queue=True,
        )
    )

    redraw_event = threading.Event()
    if redraw_event_set:
        redraw_event.set()

    completed_stats_queue = queue.Queue[str]()
    for username in completed_stats:
        completed_stats_queue.put_nowait(username)

    assert should_redraw(controller, redraw_event, completed_stats_queue) == result


@pytest.mark.parametrize(
    "loglines, resulting_controller, redraw_event_set",
    (
        (
            (f"{CHAT}[MVP+] Player1: hows ur day?",),
            MockedController(),
            False,
        ),
        (
            (f"{CHAT}Player1 has joined (1/16)!",),
            MockedController(
                state=create_state(lobby_players={"Player1"}, in_queue=True)
            ),
            True,
        ),
    ),
)
def test_process_loglines(
    loglines: tuple[str],
    resulting_controller: OverlayController,
    redraw_event_set: bool,
) -> None:
    controller = MockedController()

    redraw_event = threading.Event()

    process_loglines(loglines, redraw_event, controller)
    assert controller == resulting_controller
    assert redraw_event.is_set() == redraw_event_set


@pytest.mark.parametrize("winstreak_api_enabled", (True, False))
@pytest.mark.parametrize("estimated_winstreaks", (True, False))
def test_get_and_cache_stats(
    winstreak_api_enabled: bool, estimated_winstreaks: bool
) -> None:
    base_user = test_get_stats.users["NickedPlayer"]

    # For typing
    assert base_user.playerdata is not None

    playerdata_without_winstreaks: dict[str, Any] = {
        **base_user.playerdata,
        "stats": {"Bedwars": {}},
    }
    playerdata_with_winstreaks = {
        **base_user.playerdata,
        "stats": {
            "Bedwars": {
                "winstreak": 10,
                "eight_one_winstreak": 10,
                "eight_two_winstreak": 10,
                "four_three_winstreak": 10,
                "four_four_winstreak": 10,
            }
        },
    }

    user = (
        replace(base_user, playerdata=playerdata_with_winstreaks)
        if winstreak_api_enabled
        else replace(base_user, playerdata=playerdata_without_winstreaks)
    )
    controller = test_get_stats.make_scenario_controller(user)

    controller.get_estimated_winstreaks = (
        lambda uuid: (
            make_winstreaks(overall=100, solo=100, doubles=100, threes=100, fours=100),
            True,
        )
        if estimated_winstreaks
        else (MISSING_WINSTREAKS, False)
    )

    completed_queue = queue.Queue[str]()

    # For typing
    assert user.nick is not None

    get_stats_and_winstreak(user.nick, completed_queue, controller)

    # One update for getting the stats
    assert completed_queue.get_nowait() == user.nick

    # One update for getting estimated winstreaks
    if not winstreak_api_enabled and estimated_winstreaks:
        assert completed_queue.get_nowait() == user.nick
    else:
        with pytest.raises(queue.Empty):
            completed_queue.get_nowait()