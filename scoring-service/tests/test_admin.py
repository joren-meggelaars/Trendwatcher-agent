from tests.conftest import ADMIN_TEST_PASSWORD, ADMIN_TEST_USERNAME


def test_unauthenticated_visitor_is_redirected_to_login(client):
    resp = client.get("/admin/sources", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/admin/login")


def test_unauthenticated_items_and_batch_add_also_redirect(client):
    for path in ("/admin/items", "/admin/items/batch-add"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/admin/login")


def test_wrong_password_shows_friendly_error_not_500(client):
    resp = client.post(
        "/admin/login",
        data={"username": ADMIN_TEST_USERNAME, "password": "totally-wrong"},
    )
    assert resp.status_code == 401
    assert "Ongeldige gebruikersnaam of wachtwoord" in resp.text

    # And still logged out afterwards.
    resp = client.get("/admin/sources", follow_redirects=False)
    assert resp.status_code == 303


def test_login_then_pages_reachable_then_logout_blocks_again(client):
    login_resp = client.post(
        "/admin/login",
        data={"username": ADMIN_TEST_USERNAME, "password": ADMIN_TEST_PASSWORD},
    )
    assert login_resp.status_code == 200  # followed redirect to /admin/sources
    assert "Bronnen" in login_resp.text

    for path in ("/admin/sources", "/admin/items", "/admin/items/batch-add"):
        resp = client.get(path)
        assert resp.status_code == 200

    logout_resp = client.post("/admin/logout", follow_redirects=False)
    assert logout_resp.status_code == 303
    assert logout_resp.headers["location"].startswith("/admin/login")

    resp = client.get("/admin/sources", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/admin/login")


def test_sources_form_creates_a_new_source_visible_after_reload(admin_client):
    resp = admin_client.post(
        "/admin/sources",
        data={"url": "https://admin-added.example.com", "type": "blog"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp = admin_client.get("/admin/sources")
    assert resp.status_code == 200
    assert "admin-added.example.com" in resp.text


def test_override_source_status_updates_existing_source(admin_client):
    create_resp = admin_client.post(
        "/sources", json={"url": "https://override-test.example.com", "type": "blog"}
    )
    source_id = create_resp.json()["id"]
    assert create_resp.json()["status"] == "kandidaat"

    resp = admin_client.post(
        f"/admin/sources/{source_id}/override-status",
        data={"new_status": "actief"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    check_resp = admin_client.get("/sources")
    updated = next(s for s in check_resp.json() if s["id"] == source_id)
    assert updated["status"] == "actief"


def test_batch_add_reports_successes_and_a_clear_failure(admin_client, monkeypatch):
    import app.admin as admin_module
    from app import safe_fetch

    def _fake_fetch(url, max_bytes):
        if "unreachable" in url:
            raise safe_fetch.FetchError("kon geen verbinding maken")
        html = f"<html><head><title>Title for {url}</title></head><body>Some article body text.</body></html>"
        return safe_fetch.FetchResult(html.encode(), url, 200, "text/html; charset=utf-8")

    monkeypatch.setattr(admin_module.safe_fetch, "fetch", _fake_fetch)

    resp = admin_client.post(
        "/admin/items/batch-add",
        data={
            "urls": "\n".join(
                [
                    "https://example.com/article-1",
                    "https://example.com/article-2",
                    "https://unreachable.example.com/article-3",
                ]
            )
        },
    )
    assert resp.status_code == 200
    assert resp.text.count('class="status status-actief">OK<') == 2
    assert resp.text.count('class="status status-gedeactiveerd">Fout<') == 1
    assert "Kon content niet ophalen" in resp.text


def test_batch_add_flags_a_non_http_url_without_crashing(admin_client):
    resp = admin_client.post("/admin/items/batch-add", data={"urls": "not-a-url"})
    assert resp.status_code == 200
    assert "Ongeldige URL" in resp.text


def test_settings_page_shows_defaults_when_unauthenticated_redirects(client):
    resp = client.get("/admin/settings", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/admin/login")


def test_settings_page_shows_current_values(admin_client):
    resp = admin_client.get("/admin/settings")
    assert resp.status_code == 200
    assert 'value="7"' in resp.text  # default digest_hour
    assert 'value="5"' in resp.text  # default digest_top_n
    # default digest_days (mon-fri) checked, weekend not
    for code in ("mon", "tue", "wed", "thu", "fri"):
        assert f'value="{code}" checked' in resp.text
    assert 'value="sat" checked' not in resp.text and 'value="sun" checked' not in resp.text


def test_settings_update_persists_and_clamps_out_of_range_values(admin_client):
    resp = admin_client.post(
        "/admin/settings",
        data={"digest_hour": "9", "digest_top_n": "8", "days": ["mon", "wed", "fri"]},
    )
    assert resp.status_code == 200
    assert "Instellingen opgeslagen" in resp.text
    assert 'value="9"' in resp.text
    assert 'value="8"' in resp.text
    assert 'value="mon" checked' in resp.text and 'value="tue" checked' not in resp.text

    # Out-of-range values get clamped, not rejected with a 500/422.
    resp = admin_client.post(
        "/admin/settings",
        data={"digest_hour": "99", "digest_top_n": "0", "days": ["mon", "wed", "fri"]},
    )
    assert resp.status_code == 200
    assert 'value="23"' in resp.text  # clamped to max
    assert 'value="1"' in resp.text  # clamped to min

    check_resp = admin_client.get("/settings/digest")
    assert check_resp.json()["digest_hour"] == 23
    assert check_resp.json()["digest_top_n"] == 1
    assert check_resp.json()["digest_days"] == "mon,wed,fri"


def test_settings_update_with_no_days_ticked_shows_an_error_and_keeps_the_old_value(admin_client):
    admin_client.post("/admin/settings", data={"digest_hour": "9", "digest_top_n": "8", "days": ["sat"]})

    resp = admin_client.post("/admin/settings", data={"digest_hour": "10", "digest_top_n": "8", "days": []})
    assert resp.status_code == 200
    assert "Kies minstens één dag" in resp.text

    check_resp = admin_client.get("/settings/digest").json()
    assert check_resp["digest_days"] == "sat"   # unchanged
    assert check_resp["digest_hour"] == 9        # the whole save was rejected, not just the days


def test_send_now_relays_to_scheduler_and_reports_success(admin_client, monkeypatch):
    import app.admin as admin_module

    captured = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            pass

    def _fake_post(url, timeout):
        captured["url"] = url
        return _FakeResponse()

    monkeypatch.setattr(admin_module.httpx, "post", _fake_post)
    monkeypatch.setattr(admin_module.settings, "scheduler_url", "http://scheduler:8001")

    resp = admin_client.post("/admin/settings/send-now")

    assert resp.status_code == 200
    assert captured["url"] == "http://scheduler:8001/trigger/digest-now"
    assert "Digest wordt verstuurd" in resp.text


def test_send_now_shows_error_when_scheduler_unreachable(admin_client, monkeypatch):
    import httpx

    import app.admin as admin_module

    def _fake_post(url, timeout):
        raise httpx.ConnectError("connection failed", request=httpx.Request("POST", url))

    monkeypatch.setattr(admin_module.httpx, "post", _fake_post)

    resp = admin_client.post("/admin/settings/send-now")

    assert resp.status_code == 200
    assert "Kon de scheduler niet bereiken" in resp.text


# --- several sources at once ------------------------------------------------------------------------


def _make_sources(admin_client, n):
    ids = []
    for i in range(n):
        resp = admin_client.post("/sources", json={"url": f"https://bulk-{i}.example.com/feed", "type": "rss"})
        ids.append(resp.json()["id"])
    return ids


def _statuses(admin_client):
    return {s["id"]: s["status"] for s in admin_client.get("/sources").json()}


def test_sources_page_has_a_checkbox_per_row_and_a_bar_with_the_three_statuses(admin_client):
    ids = _make_sources(admin_client, 2)

    page = admin_client.get("/admin/sources").text

    for source_id in ids:
        assert f'name="ids" value="{source_id}" form="bulk-form"' in page
    assert "data-bulk-all" in page and 'action="/admin/sources/bulk-status"' in page
    for status in ("kandidaat", "actief", "gedeactiveerd"):
        assert f'name="new_status" value="{status}"' in page
    assert "/static/bulk.js" in page and "<script>" not in page  # CSP: no inline script


def test_bulk_status_changes_only_the_ticked_sources(admin_client):
    a, b, c = _make_sources(admin_client, 3)

    resp = admin_client.post("/admin/sources/bulk-status", data={"new_status": "actief", "ids": [a, c]}, follow_redirects=True)

    assert resp.status_code == 200 and "2 bronnen op actief gezet" in resp.text
    assert _statuses(admin_client) == {a: "actief", b: "kandidaat", c: "actief"}


def test_bulk_status_can_deactivate_and_go_back_to_candidate(admin_client):
    a, b = _make_sources(admin_client, 2)

    admin_client.post("/admin/sources/bulk-status", data={"new_status": "gedeactiveerd", "ids": [a, b]})
    assert set(_statuses(admin_client).values()) == {"gedeactiveerd"}

    resp = admin_client.post("/admin/sources/bulk-status", data={"new_status": "kandidaat", "ids": [a]}, follow_redirects=True)
    assert "1 bron op kandidaat gezet" in resp.text
    assert _statuses(admin_client) == {a: "kandidaat", b: "gedeactiveerd"}


def test_bulk_status_stays_on_the_filtered_tab_and_reports_what_did_not_change(admin_client):
    a, b = _make_sources(admin_client, 2)

    resp = admin_client.post(
        "/admin/sources/bulk-status", data={"new_status": "kandidaat", "ids": [a, b, 9999], "back": "kandidaat"}, follow_redirects=False
    )

    assert resp.status_code == 303 and resp.headers["location"].startswith("/admin/sources?status=kandidaat&notice=")
    page = admin_client.get(resp.headers["location"]).text
    assert "0 bronnen op kandidaat gezet" in page and "3 had die status al of bestaat niet meer" in page


def test_bulk_status_with_nothing_ticked_or_a_bad_status_changes_nothing(admin_client):
    (a,) = _make_sources(admin_client, 1)

    empty = admin_client.post("/admin/sources/bulk-status", data={"new_status": "actief"}, follow_redirects=True)
    bad = admin_client.post("/admin/sources/bulk-status", data={"new_status": "verwijderd", "ids": [a]}, follow_redirects=True)
    junk = admin_client.post("/admin/sources/bulk-status", data={"new_status": "actief", "ids": ["abc"]})

    assert "Geen bronnen geselecteerd" in empty.text and "Onbekende status" in bad.text
    assert junk.status_code == 422
    assert _statuses(admin_client) == {a: "kandidaat"}


def test_bulk_status_requires_login(client):
    resp = client.post("/admin/sources/bulk-status", data={"new_status": "actief", "ids": [1]}, follow_redirects=False)

    assert resp.status_code in (303, 401) and "login" in resp.headers.get("location", "login")


def test_bulk_status_refuses_a_post_from_another_site(admin_client):
    (a,) = _make_sources(admin_client, 1)

    resp = admin_client.post(
        "/admin/sources/bulk-status", data={"new_status": "actief", "ids": [a]}, headers={"Origin": "https://evil.example.com"}
    )

    assert resp.status_code == 403 and _statuses(admin_client) == {a: "kandidaat"}
