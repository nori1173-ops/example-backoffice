import itertools

import pytest
from daily_security_report.distribution import channels


@pytest.mark.parametrize(
    "mail,slack,destination", list(itertools.product([False, True], [False, True], ["", "D012345678"]))
)
def test_channels(mail, slack, destination):
    expected = (["email"] if mail else []) + (["slack"] if slack and destination else [])
    assert channels({"mail_enabled": mail, "slack_enabled": slack, "slack_channel_id": destination}) == expected


def test_missing_settings():
    assert channels({}) == []
