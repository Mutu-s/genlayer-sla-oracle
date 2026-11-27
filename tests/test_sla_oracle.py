import json

CONTRACT = "contracts/sla_oracle.py"
STATUS = r"status\.example\.com"
URL = "https://status.example.com/incidents/abc123"
URL2 = "https://status.example.com/incidents/def456"


def llm(affects=True, resolved=True, start="2026-10-03T14:05", end="2026-10-03T15:35", summary="RPC outage"):
    return json.dumps({
        "affects_service": affects,
        "resolved": resolved,
        "start_utc": start,
        "end_utc": end,
        "summary": summary,
    })


def deploy(direct_deploy, direct_vm, direct_owner):
    c = direct_deploy(CONTRACT)
    direct_vm.sender = direct_owner
    c.register_service("rpc-eu", "Example RPC (EU)", "status.example.com", 9990, 1000, 9900, 3000)
    return c


def file(c, vm, url=URL, **kw):
    vm.mock_web(STATUS, {"status": 200, "body": "Incident report page text"})
    vm.mock_llm(r".*", llm(**kw))
    c.file_incident("rpc-eu", url)


# ------------------------------------------------------------ registration

def test_register_and_read(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    s = c.get_service("rpc-eu")
    assert s["target_bps"] == 9990 and s["severe_bps"] == 9900
    assert s["status_domain"] == "status.example.com" and s["active"] is True


def test_register_rejects_bad_tiers(direct_deploy, direct_vm):
    c = direct_deploy(CONTRACT)
    with direct_vm.expect_revert("severe_bps < target_bps"):
        c.register_service("x", "X", "status.x.io", 9900, 1000, 9990, 3000)
    with direct_vm.expect_revert("credit_bps <= severe_credit_bps"):
        c.register_service("x", "X", "status.x.io", 9990, 5000, 9900, 1000)
    with direct_vm.expect_revert("bare host"):
        c.register_service("x", "X", "https://status.x.io", 9990, 1000, 9900, 3000)


def test_duplicate_service(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    with direct_vm.expect_revert("already registered"):
        c.register_service("rpc-eu", "Again", "status.example.com", 9990, 1000, 9900, 3000)


def test_only_provider_can_pause(direct_deploy, direct_vm, direct_owner, direct_alice):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("only the provider"):
            c.set_active("rpc-eu", False)
    c.set_active("rpc-eu", False)
    with direct_vm.expect_revert("not accepting claims"):
        c.file_incident("rpc-eu", URL)


# ------------------------------------------------------- incident filing

def test_incident_counts_downtime_and_breach(direct_deploy, direct_vm, direct_owner, direct_alice):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    with direct_vm.prank(direct_alice):
        file(c, direct_vm)  # 90 minutes
    inc = c.get_incident("rpc-eu", URL)
    assert inc["affected"] is True and inc["outage_minutes"] == 90
    assert inc["period"] == "2026-10" and inc["claimant"].lower() == "0x" + bytes(direct_alice).hex()

    r = c.get_period_report("rpc-eu", "2026-10")
    # October: 31 days = 44640 min; 90 min down -> 99.79 % -> below 99.90 target
    assert r["minutes_in_period"] == 44640 and r["downtime_minutes"] == 90
    assert r["uptime_bps"] == (44640 - 90) * 10000 // 44640 == 9979
    assert r["tier"] == "breach" and r["credit_bps"] == 1000
    assert r["incidents"] == [URL]


def test_incidents_accumulate_into_severe(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    file(c, direct_vm)  # 90 min
    direct_vm.clear_mocks()
    file(c, direct_vm, url=URL2, start="2026-10-20T00:00", end="2026-10-20T08:00")  # 480 min
    r = c.get_period_report("rpc-eu", "2026-10")
    assert r["downtime_minutes"] == 570 and r["uptime_bps"] == 9872
    assert r["tier"] == "severe" and r["credit_bps"] == 3000
    assert len(r["incidents"]) == 2


def test_timezone_is_normalised_to_utc(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    file(c, direct_vm, start="2026-11-01T01:30+03:00", end="2026-11-01T02:00+03:00")
    inc = c.get_incident("rpc-eu", URL)
    # starts 31 Oct 22:30 UTC -> counted in October
    assert inc["start_utc"] == "2026-10-31T22:30" and inc["period"] == "2026-10"
    assert inc["outage_minutes"] == 30


def test_unrelated_incident_is_recorded_but_not_counted(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    file(c, direct_vm, affects=False, start=None, end=None)
    assert c.get_incident("rpc-eu", URL)["affected"] is False
    r = c.get_period_report("rpc-eu", "2026-10")
    assert r["downtime_minutes"] == 0 and r["tier"] == "ok" and r["credit_bps"] == 0
    with direct_vm.expect_revert("already filed"):
        c.file_incident("rpc-eu", URL)


def test_unresolved_incident_is_rejected(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    with direct_vm.expect_revert("not resolved"):
        file(c, direct_vm, resolved=False, end=None)


def test_url_must_be_on_status_domain(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    with direct_vm.expect_revert("status domain"):
        c.file_incident("rpc-eu", "https://evil.com/status.example.com/x")
    with direct_vm.expect_revert("status domain"):
        c.file_incident("rpc-eu", "https://status.example.com.evil.com/x")
    with direct_vm.expect_revert("https"):
        c.file_incident("rpc-eu", "http://status.example.com/x")


def test_duplicate_incident_and_fragment(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    file(c, direct_vm)
    with direct_vm.expect_revert("already filed"):
        c.file_incident("rpc-eu", URL + "#updates")


def test_implausible_extraction_rejected(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    with direct_vm.expect_revert("not after start"):
        file(c, direct_vm, start="2026-10-03T15:00", end="2026-10-03T14:00")


# -------------------------------------------------- validator consensus

def test_validator_accepts_small_differences(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    file(c, direct_vm)  # leader: 14:05 -> 15:35
    direct_vm.clear_mocks()
    direct_vm.mock_web(STATUS, {"status": 200, "body": "same page"})
    direct_vm.mock_llm(r".*", llm(start="2026-10-03T14:00", end="2026-10-03T15:40"))
    assert direct_vm.run_validator() is True


def test_validator_rejects_inflated_outage(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    file(c, direct_vm, start="2026-10-03T14:05", end="2026-10-03T18:05")  # leader claims 4h
    direct_vm.clear_mocks()
    direct_vm.mock_web(STATUS, {"status": 200, "body": "same page"})
    direct_vm.mock_llm(r".*", llm(start="2026-10-03T14:05", end="2026-10-03T15:35"))
    assert direct_vm.run_validator() is False


def test_validator_rejects_wrong_verdict(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    file(c, direct_vm)
    direct_vm.clear_mocks()
    direct_vm.mock_web(STATUS, {"status": 200, "body": "same page"})
    direct_vm.mock_llm(r".*", llm(affects=False, start=None, end=None))
    assert direct_vm.run_validator() is False


def test_validator_agrees_with_leader_error_only_if_it_also_fails(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    file(c, direct_vm)
    direct_vm.clear_mocks()
    direct_vm.mock_web(STATUS, {"status": 200, "body": "page"})
    direct_vm.mock_llm(r".*", llm(resolved=False, end=None))
    assert direct_vm.run_validator(leader_error=Exception("[EVIDENCE] incident is not resolved yet")) is True
    direct_vm.clear_mocks()
    direct_vm.mock_web(STATUS, {"status": 200, "body": "page"})
    direct_vm.mock_llm(r".*", llm())
    assert direct_vm.run_validator(leader_error=Exception("[EVIDENCE] incident is not resolved yet")) is False


def test_report_validates_period(direct_deploy, direct_vm, direct_owner):
    c = deploy(direct_deploy, direct_vm, direct_owner)
    with direct_vm.expect_revert("YYYY-MM"):
        c.get_period_report("rpc-eu", "2026-13")
    r = c.get_period_report("rpc-eu", "2028-02")  # leap year, no incidents
    assert r["minutes_in_period"] == 29 * 1440 and r["uptime_bps"] == 10000 and r["tier"] == "ok"
