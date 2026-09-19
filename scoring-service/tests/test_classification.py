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
        # innovation: new products, services and developments
        "Microsoft unveils Security Copilot agents for Defender and Sentinel",
        "Zscaler launches new AI-powered SSE platform",
        "Cisco introduces new SOAR capabilities in Splunk",
        "Microsoft Sentinel data lake now generally available",
        "Defender XDR: public preview of automatic attack disruption for SAP",
        "Microsoft Entra adds new capabilities for passkeys",
        "Palo Alto Networks debuts next-generation firewall",
        "Netskope announces new zero trust offering",
        "CrowdStrike releases new Falcon module to protect AI agents",
        "Zscaler unveils new platform to stop ransomware",  # a defence product, not an attack story
        "Introducing context-aware vulnerability discovery and remediation with Cloudflare",
        "Monthly news - July 2026",
        "What's new in Microsoft Defender?",
        "Microsoft Defender now integrates with Dragos, Forescout and Armis",
        "Vendor lanceert nieuwe dienst voor SOC-monitoring",
        # new players, trends, protocols and standards
        "New startup emerges from stealth with SOC automation platform",
        "Top security trends for 2027",
        "IETF publishes RFC 9999 for post-quantum TLS",
        "NIST finalizes post-quantum cryptography standards",
        # market analysis
        "AI Security Spending Jumps as Fear Outpaces Proof of Value",
        "EY Survey Finds Autonomous AI Implementation Outpaces Oversight",
        "Palo Alto Networks Named a Leader in the 2026 Gartner Magic Quadrant",
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
        # the words of a product story, in an attack or patch story
        "Hackers launch new phishing campaign against banks",
        "Ransomware gang launches new leak site",
        "Critical vulnerability introduces risk to Cisco routers",
        "Attackers release new exploit for Citrix flaw",
        "Hackers introduce new backdoor in npm packages",
        "Threat actors announce new data leak on darknet forum",
        "Apple releases new iOS version to fix zero-day",
        "Fortinet releases fix for critical flaw",
        "Chrome update fixes actively exploited zero-day",
        "Critical RCE flaw in Cisco ISE now exploited in attacks",
        "New malware family targets Linux servers",
        # generic words that only mean something outside threat talk
        "Ransomware trends in Q3: attacks on hospitals surge",
        "Emerging threats to watch this year",
        "Zero-day in Netgear routers allows remote takeover",
        "Account takeover attacks surge",
        "SOC analysts overwhelmed by alert volume",
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
