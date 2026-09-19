import pytest

from app.classification import classify, market_strength


@pytest.mark.parametrize(
    "title",
    [
        "Palo Alto Networks acquires CyberArk for $25 billion",
        "Cyera raises $300M Series D at $3B valuation",
        "Gartner Magic Quadrant for endpoint protection 2026",
        "Security vendor files for IPO after record revenue",
        "Managed security provider merges with rival MSSP",
        "Zwitserse beveiliger neemt Nederlandse MSP over",
        "Marktaandeel van cloudbeveiliging groeit sterk",
        "Overheid investeert 50 miljoen in cyberweerbaarheid",
        "Cybersecurity M&A Roundup: 33 Deals Announced in August 2026",
    ],
)
def test_market_developments_are_classified_as_markt(title):
    assert classify(title, "") == "markt"


@pytest.mark.parametrize(
    "title",
    [
        "Critical zero-day in Fortinet firewalls actively exploited",
        "Ransomware gang hits hospital chain, patient data leaked",
        "NCSC waarschuwt voor kwetsbaarheid in Citrix NetScaler",
        "Microsoft patches 80 flaws in September Patch Tuesday",
        "New phishing campaign abuses OAuth consent screens",
        "CVE-2026-1234: remote code execution in OpenSSH",
    ],
)
def test_plain_security_news_is_classified_as_nieuws(title):
    assert classify(title, "") == "nieuws"


def test_word_acquired_alone_does_not_make_an_incident_market_news():
    # "acquired credentials" is an intrusion detail, not an acquisition.
    title = "Attackers acquired credentials and moved laterally"
    assert classify(title, "The attackers acquired access to the network.") == "nieuws"


def test_a_single_summary_hit_is_not_enough_but_two_are():
    assert classify("Nieuwe kwetsbaarheid gevonden", "Het bedrijf noemt de investering.") == "nieuws"
    assert (
        classify(
            "Nieuwe kwetsbaarheid gevonden",
            "Het bedrijf noemt de investering en de hogere omzet.",
        )
        == "markt"
    )


def test_title_hit_weighs_more_than_summary_hit():
    assert market_strength("Startup raises $10M", "") > market_strength("Startup nieuws", "raises $10M")


def test_repeated_pattern_counts_once():
    assert market_strength("", "funding funding funding funding") == market_strength("", "funding")


def test_empty_input_is_nieuws():
    assert classify("", "") == "nieuws"
    assert market_strength("", "") == 0
