import pytest

from prism.overlay.state import OverlayState
from tests.prism.overlay.utils import create_state


@pytest.mark.parametrize(
    "before, after",
    (
        (create_state(), create_state()),
        (create_state(party_members={"OwnUsername", "Player2"}), create_state()),
        (
            OverlayState(party_members=frozenset(), own_username="OwnUsername"),
            create_state(),
        ),
        (
            create_state(own_username=None, party_members=set()),
            create_state(own_username=None, party_members=set()),
        ),
    ),
)
def test_clear_party(before: OverlayState, after: OverlayState) -> None:
    assert before.clear_party() == after
